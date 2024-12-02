# Development - Contributing

Issues and pull requests are more than welcome: https://github.com/developmentseed/titiler-openeo/issues

**dev install**

```bash
git clone https://github.com/developmentseed/titiler-openeo.git
cd titiler

python -m pip install -e ".[test,dev]"
```

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
git clone https://github.com/developmentseed/titiler-openeo.git
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
```
