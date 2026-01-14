# Release Process

This project uses [Release Please](https://github.com/googleapis/release-please) to automate releases based on [Conventional Commits](https://www.conventionalcommits.org/).

## Automated Release Process

### 1. Create Pull Requests with Conventional Titles

**PR titles are automatically validated** and must follow conventional commit format:

```bash
# Valid PR titles:
feat: add new process for NDVI calculation
fix: resolve memory leak in tile processing
feat!: change API endpoint structure (breaking change)
docs: update installation guide
perf: optimize raster processing pipeline
chore(deps): update dependencies
fix(auth): resolve OIDC timeout issues
```

**The validation workflow will block merging** if the PR title doesn't follow the format.

### 2. Individual Commits (Optional)

Within PRs, you can use any commit style. Only the **PR title matters** for changelog generation since we use squash-merge.

However, if you prefer conventional commits throughout:

```bash
git commit -m "feat: add new process for NDVI calculation"
git commit -m "fix: resolve memory leak in tile processing"
git commit -m "docs: update installation guide"
```

### 3. Release Please Automation

When PRs are merged to `main`:

1. **Release Please** analyzes PR titles (which become commit messages via squash-merge)
2. Creates/updates a **Release PR** with:
   - Updated [CHANGELOG.md](CHANGELOG.md) based on PR titles
   - Bumped version in [titiler/openeo/__init__.py](titiler/openeo/__init__.py)
   - Updated [deployment/k8s/charts/Chart.yaml](deployment/k8s/charts/Chart.yaml)

3. When the Release PR is **merged**:
   - Creates a GitHub release with changelog
   - Publishes to PyPI automatically  
   - Builds and pushes Docker images to GHCR

### 4. Manual Release Steps (Optional)

If you need to manually trigger a release or update chart versions:

```bash
# Update Chart.yaml if needed
# deployment/k8s/charts/Chart.yaml:
# - Increment chart version by 0.0.1
# - appVersion will be updated automatically by release-please
```

## PR Title Format (Enforced)

**All PR titles are automatically validated** and must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

**✅ Examples:**
```
feat: add comprehensive parameter management system
fix(auth): resolve OIDC timeout issues  
docs: update installation guide
perf(processes): optimize raster processing pipeline
feat!: change API endpoint structure (breaking change)
chore(deps): update dependencies to latest versions
```

**❌ Invalid (will block merge):**
```
Add new feature           # Missing type
Fix bug                   # Missing descriptive message  
FIX: resolve issue        # Type should be lowercase
feat: Add new feature     # Description should not start with capital
Update docs               # Missing type
```

### PR Title Types

- **feat**: New feature (triggers minor version bump)
- **fix**: Bug fix (triggers patch version bump)  
- **feat!** or **fix!**: Breaking change (triggers major version bump)
- **docs**: Documentation changes
- **style**: Code style changes (formatting, etc.)
- **refactor**: Code refactoring without functional changes
- **perf**: Performance improvements
- **test**: Test changes
- **chore**: Build process or auxiliary tool changes
- **ci**: CI configuration changes
- **revert**: Revert previous changes

### Optional Scopes

- **api**: API-related changes
- **auth**: Authentication/authorization  
- **processes**: OpenEO process implementations
- **store**: Data store implementations
- **docker**: Docker-related changes
- **helm**: Kubernetes/Helm changes
- **docs**: Documentation
- **deps**: Dependency updates
- **ci**: CI/CD pipeline

## Repository Settings

The repository is configured with:

- ✅ **Squash-merge only** (PR title becomes commit message)
- ✅ **PR title validation** (blocks merge if invalid)
- ✅ **Automatic changelog generation** from PR titles
- ✅ **Branch protection** requiring PR title validation

## Version Numbering

We follow [Semantic Versioning (SemVer)](https://semver.org/):

- **MAJOR**: Breaking changes (`feat!`, `fix!` commits)
- **MINOR**: New features (`feat` commits)  
- **PATCH**: Bug fixes (`fix` commits)

## Helm Chart Versioning

The Helm chart version is managed separately and should be incremented manually when needed:

- Chart version increments by 0.0.1 for each release
- `appVersion` is updated automatically by release-please to match the package version

## Emergency Releases

For urgent fixes outside the normal process:

1. Create a hotfix branch from main
2. Make the fix with proper conventional commit message
3. Create PR to main
4. The release-please process will handle the rest automatically
