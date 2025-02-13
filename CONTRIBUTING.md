# Development - Contributing

Issues and pull requests are more than welcome: https://github.com/sentinel-hub/titiler-openeo/issues

**dev install**

```bash
git clone https://github.com/sentinel-hub/titiler-openeo.git
cd titiler

python -m pip install -e ".[test,dev]"
```

**Authentication Testing with Keycloak**

The project includes a Keycloak instance for testing OpenID Connect authentication:

1. Start the development environment:
```bash
docker compose up
```

2. Access Keycloak admin console at http://localhost:8082/admin
   - Username: `admin`
   - Password: `admin`

3. Create a new client:
   - Go to "Clients" → "Create client"
   - Client ID: `titiler-openeo`
   - Client type: `OpenID Connect`
   - Click "Next"
   - Enable "Client authentication"
   - Enable "Authorization"
   - Click "Save"

4. Configure client settings:
   - Valid redirect URIs: `http://localhost:8081/*` and `http://localhost:8080/*` for the openEO editor
   - Web origins: `http://localhost:8081` and `http://localhost:8080` for the openEO editor
   - Click "Save"

5. Create a test user:
   - Go to "Users" → "Add user"
   - Username: `test`
   - Email: `test@example.com`
   - Click "Create"
   - Go to "Credentials" tab
   - Set password: `test123`
   - Disable "Temporary"
   - Click "Save password"

The Keycloak server will be available at http://localhost:8082 for testing OIDC authentication flows.

**pre-commit**

This repo is set to use `pre-commit` to run *isort*, *flake8*, *pydocstring*, *black* ("uncompromising Python code formatter") and mypy when committing new code.

```bash
pre-commit install
```

### Run tests

```
python -m pytest --cov=titiler.openeo --cov-report=xml --cov-append --cov-report=term-missing
```

### Docs

```bash
git clone https://github.com/sentinel-hub/titiler-openeo.git
cd titiler
python -m pip install -e ".[docs]"
```

Hot-reloading docs:

```bash
mkdocs serve -f docs/mkdocs.yml
```

To manually deploy docs (note you should never need to do this because Github
Actions deploys automatically for new commits.):

```bash
mkdocs gh-deploy -f docs/mkdocs.yml
