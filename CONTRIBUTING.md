# Development - Contributing

Issues and pull requests are more than welcome: https://github.com/sentinel-hub/titiler-openeo/issues

## Local environment

It is easiest to bootstrap a development environment with [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync --group dev --group test --group docs --all-extras
cp .env.eoapi .env
export $(cat .env | xargs)
uvicorn titiler.openeo.main:app --host 0.0.0.0 --port 8081
```

## Pre-commit hooks

This repo is set to use `pre-commit` to run *isort*, *flake8*, *pydocstring*, *black*, and *mypy* when committing new code.

```bash
pre-commit install
```

## Running tests

```bash
python -m pytest
```

Add coverage options (e.g. `--cov=titiler.openeo`) when validating locally before release.

## Use the openEO editor

To use the openEO editor, use Docker Compose to start all services:

```bash
docker compose up
```

This will start:
- API service at http://localhost:8081
- openEO Web Editor at http://localhost:8080
- Keycloak at http://localhost:8082

Access the editor at http://localhost:8080 and set the backend URL to http://localhost:8081. For authentication setup and testing, see the [Admin Guide](https://sentinel-hub.github.io/titiler-openeo/admin-guide/#authentication).

### Authentication testing with Keycloak

The project includes a Keycloak instance for testing OpenID Connect authentication. After starting the stack with `docker compose up`, configure Keycloak as follows:

1. Access the Keycloak admin console at http://localhost:8082/admin
   - Username: `admin`
   - Password: `admin`
2. Create a new client:
   - Go to "Clients" → "Create client"
   - Client ID: `titiler-openeo`
   - Client type: `OpenID Connect`
   - Click "Next"
   - Enable "Client authentication"
   - Enable "Authorization"
   - Click "Save"
3. Configure client settings:
   - Valid redirect URIs: `http://localhost:8080/*` for the openEO editor
   - Web origins: `http://localhost:8080` for the openEO editor
   - Click "Save"

The environment includes several pre-configured settings:
- GDAL optimization settings for performance
- Debug mode enabled
- STAC API endpoint set to https://stac.eoapi.dev
- Keycloak OIDC configuration

4. Create a test user:
   - Go to "Users" → "Add user"
   - Username: `test`
   - Email: `test@example.com`
   - Click "Create"
   - Go to "Credentials" tab
   - Set password: `test123`
   - Disable "Temporary"
   - Click "Save password"

The Keycloak server will be available at http://localhost:8082 for testing OIDC authentication flows.

## Docs

Install the documentation extras with `uv`:

```bash
uv sync --group docs
```

Hot-reloading docs:

```bash
mkdocs serve -f docs/mkdocs.yml
```

To manually deploy docs (note you should never need to do this because GitHub
Actions deploys automatically for new commits.):

```bash
mkdocs gh-deploy -f docs/mkdocs.yml
```
