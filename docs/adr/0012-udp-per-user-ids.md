# ADR 0012 — User-defined process ids are unique per user

- **Status:** Accepted; amended (2026-10-29), see [§7](#7-amendment-2026-10-29--the-migration-runs-in-the-schema-command)
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
  old table. These steps run in a savepoint. The `pysqlite` driver does not
  open a transaction for DDL, so without the savepoint a failure part way
  leaves an empty table with the new key. The next start then does not
  migrate, and the old rows stay hidden in the renamed table.
- **DuckDB:** DuckDB cannot drop a primary key. The store rebuilds the table
  in one transaction.
- **Local JSON:** the store reads the old flat layout
  (`{udp_id: {"user_id": ..., ...}}`) and keeps it per user in memory. It
  writes the new layout at the next change. An old entry has a string
  `user_id`, and a new entry maps UDP ids to dicts, thus the store cannot
  mistake one layout for the other.

The old key made the ids unique across all users. Thus no two old rows can
have the same `(user_id, id)`, and the copy cannot fail on a duplicate.

### 4.1 No rolling upgrade

The advisory lock controls only the startup of new workers. It does not stop
old workers that continue to run during a rolling update. Their ORM model
keys `udp_definitions` on `id` alone. After the migration, when two users
have the same id:

- `delete_udp` selects the row by `id` and `user_id`, but `session.delete`
  sends `DELETE ... WHERE id = ?`. Thus the rows of both users are deleted.
- `upsert_udp` selects by `id` alone and gets two rows, thus it fails with
  `MultipleResultsFound`.

Thus the upgrade must stop all old workers before the new workers start. The
Helm chart has a `strategy` value for this (`type: Recreate`). The admin
guide gives the upgrade steps.

We did not use a staged migration over two releases (first delete and update
by `(user_id, id)` with the old key, then change the key). It makes rolling
upgrades safe, but it delays the fix for one release, and it is safe only if
operators do not skip the first release. DuckDB and the local JSON store are
not affected, because only one process can use them.

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
- On SQL stores, the upgrade cannot be a rolling update (§4.1), thus it has
  downtime.

## 7. Amendment (2026-10-29) — the migration runs in the schema command

Since #479, the stores run no DDL on a server database when they start. On
PostgreSQL, the migration of §4 runs in
`python -m titiler.openeo.services.create_schema`, which the Helm chart runs
in a `post-install,post-upgrade` Job, and takes no advisory lock: only that
command runs DDL. Outside the chart, operators run the command (admin guide).
A SQLite store still migrates when it starts.

§4.1 still holds: the Job runs after the new pods start, and during a rolling
update old pods can still serve after it, so this upgrade uses
`strategy.type: Recreate`. Until the Job has run, the table keeps the old key,
so two users cannot yet save the same id.
