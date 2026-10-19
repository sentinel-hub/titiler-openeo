# ADR 0012 — User-defined process ids are unique per user

- **Status:** Accepted
- **Date:** 2026-10-19
- **Deciders:** @emmanuelmathot
- **Related:** [#469](https://github.com/sentinel-hub/titiler-openeo/issues/469)

---

## 1. Context

### 1.1 The bug

`PUT /process_graphs/{process_graph_id}` refused an id that another user
already had:

```
400 UDP new_project does not belong to user <user_id>
```

All three UDP stores (SQLAlchemy, DuckDB, local JSON) used `id` as the only
key. `upsert_udp` looked for the id across all users. Thus on a shared
deployment, the first user to save a common name (`ndvi`, `new_project`) kept
that name for all users. Reads and deletes already filtered on `id` and
`user_id`, so only storage and upsert were global.

### 1.2 What the openEO API says

- The client selects the id. It is the path parameter of
  `PUT /process_graphs/{process_graph_id}`, a "Per-user unique identifier for
  a user-defined process", and it must match `^\w+$`.
- The id "MUST be unique per user across the user-defined processes".
- The id is not a title. A title goes in `summary` and `description`. The id
  is the name with which a process graph calls the UDP, as a function name:

  ```json
  {"process_id": "ndvi", "namespace": "user", "arguments": {}}
  ```

- The API has no rename operation. To rename a UDP, a client deletes it and
  then saves it with the new id.

Thus the server cannot make the id (for example, a UUID). Users must be able
to call their processes by name.

## 2. Options

| Option | Schema | Advantages | Disadvantages |
| --- | --- | --- | --- |
| **A. Composite natural key** | primary key `(user_id, id)` | Simple. Agrees with the API. Smallest migration. | A rename changes the key. The API has no rename, so this has no effect now. |
| **B. Surrogate key** | primary key `uuid`, plus `UNIQUE (user_id, id)` | A stable internal reference for other tables that point to UDPs. | A column that no API uses now. A larger migration. |

## 3. Decision

We use **option A**. All stores key UDPs on `(user_id, id)`:

- **SQLAlchemy:** `PrimaryKeyConstraint("user_id", "id")` on
  `udp_definitions`.
- **DuckDB:** `PRIMARY KEY (user_id, id)` on `udp_definitions`.
- **Local JSON:** UDPs are kept as `{user_id: {udp_id: data}}`. JSON keys
  must be strings, thus a tuple key is not possible.

`upsert_udp` finds the existing row by `id` and `user_id`. A user cannot
change the UDP of a different user, because the two rows are different.

A name that is unique per user is usual practice, as a file path in a home
directory or a function name in a module. A name that is unique across all
users is the error that this ADR corrects.

## 4. Migration

Each store changes an older layout to the new key when it starts. Operators
do not have to do a manual step.

- **SQLAlchemy, PostgreSQL:** `create_schema` drops the old primary key
  constraint and adds `PRIMARY KEY (user_id, id)`. This occurs under the same
  advisory lock as the schema creation (#274). Thus, when replicas start
  together, only one of them migrates the table. The others then find the new
  key and do nothing.
- **SQLAlchemy, SQLite:** SQLite cannot change a primary key. The store
  renames the old table, creates the new table, copies the rows and drops the
  old table.
- **DuckDB:** DuckDB cannot drop a primary key. The store rebuilds the table
  in one transaction.
- **Local JSON:** the store reads the old flat layout
  (`{udp_id: {"user_id": ..., ...}}`) and keeps it per user in memory. It
  writes the new layout at the next change. An old entry has a string
  `user_id`, and a new entry maps UDP ids to dicts, thus the store cannot
  mistake one layout for the other.

The old key made the ids unique across all users. Thus no two old rows can
have the same `(user_id, id)`, and the copy cannot fail on a duplicate.

## 5. When to look at option B again

Add a surrogate key when a different table or resource must point to a UDP
and must not change when the UDP gets a new id. For example:

- sharing a UDP with other users, or a public UDP namespace
  ([openeo-api#85](https://github.com/Open-EO/openeo-api/issues/85)),
- a UDP version history,
- a rename operation in a future version of the openEO API.

Then add a `uuid` column as the primary key and keep `(user_id, id)` as a
unique constraint. The API continues to use the `id`.

## 6. Consequences

- Two users can save UDPs with the same id. Each user sees and calls only
  their own UDP.
- The error `UDP <id> does not belong to user <user_id>` no longer occurs.
- The first start after the upgrade changes the `udp_definitions` table. On a
  large table, this start can take more time.
- A downgrade to an older version is not supported after two users save the
  same id. The old key cannot hold the two rows.
