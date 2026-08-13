# Branch Strategy

This fork uses a dual-branch strategy for development and upstream synchronization.

## Branches

### 🔵 `main` - Upstream Mirror
- **Purpose**: Stay in sync with upstream (akashgit/remote-factory)
- **Updates**: Pull from upstream regularly
- **CI**: Runs on upstream only, disabled on fork
- **Never commit directly to this branch**

### 🟢 `lumen` - Development Branch
- **Purpose**: All development work and experiments
- **Based on**: `main` branch
- **CI**: Disabled to prevent email spam
- **Daily work happens here**

### 🟡 `fix/*` - Feature/Fix Branches (optional)
- Created from `lumen` for specific features
- Merged back to `lumen` when complete

## Workflow

### Daily Development
```bash
# Work on lumen branch
git checkout lumen

# Make changes, commit, push
git add .
git commit -m "feat: your changes"
git push origin lumen
```

### Syncing with Upstream
```bash
# Update main from upstream
git checkout main
git pull upstream main
git push origin main

# Merge upstream changes into lumen
git checkout lumen
git merge main

# Resolve conflicts if any
git push origin lumen
```

### Creating a Feature Branch (optional)
```bash
# Create from lumen
git checkout lumen
git checkout -b feature/my-feature

# Work and commit
git add .
git commit -m "feat: implement feature"
git push -u origin feature/my-feature

# Merge back when done
git checkout lumen
git merge feature/my-feature
git push origin lumen
```

## Remotes

- `origin`: Your fork (ash-ding/remote-factory)
- `upstream`: Original repo (akashgit/remote-factory)

## Current State

- ✅ `main` branch synced with upstream/main (f27858e2)
- ✅ `lumen` branch created with CI disabled
- ✅ Ready for development
