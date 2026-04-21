# functions - Fission FaaS Functions Documentation - LLM Starting Point

**This is the primary document for LLM-assisted development on this repository.**

All essential information is consolidated here.

---

## Project Overview

**functions** is a repository managing Fission-compatible FaaS (Function as a Service) functions for deployment to a Fission cluster. It uses GitHub Actions to build, test, and release versioned function packages.

This repository contains serverless functions that can be deployed to Fission for:
- HTTP request handlers (webhooks, APIs, web endpoints)
- Event-driven functions (Kubernetes events, cloud events)
- Background tasks (processing jobs, scheduled work)
- Utility functions (helpers, transformations, integrations)
- Integrations with external services (databases, APIs, messaging)

**Deployment target:** Fission cluster — containerized function deployment platform on Kubernetes.

---

## CRITICAL RULES

### 1. NEVER Commit Unencrypted Secrets

**All secrets MUST be handled securely. This rule has no exceptions.**

- Function code should never contain hardcoded credentials, API keys, or secrets.
- Use environment variables injected by Fission at runtime.
- Use Kubernetes Secrets mounted into the function pod.
- Never commit `.env` files, `*.key`, or any credential files.
- Test functions should use mock data or fixture files, never real secrets.

**Before staging any changes, verify:**

```bash
# Check for suspicious patterns in function code
grep -r -i "password\|secret\|key\|token" functions/ | grep -v "test_\|mock_\|fixture"
```

**If you find hardcoded secrets, remove them immediately:**
1. Replace with environment variable references
2. Update function documentation to list required env vars
3. Commit the fix without ever exposing the real values

### 2. NEVER Perform Destructive Git Operations

**Multiple agents or sessions may work in this repository.**

**FORBIDDEN:**
- `git checkout .` — discards uncommitted changes
- `git reset --hard` — destroys work
- `git clean -fd` — deletes untracked files
- Any command that might delete work in progress

**REQUIRED:**
- Revert files one at a time with explicit user confirmation
- Always check `git status` before reverting anything
- Use `git restore <file>` for single file reversions

### 3. Function Versions Must Be Explicit

**Never use floating versions or `latest` references.** Always pin:
- Runtime version in `.fission/runtime.txt` (e.g., `python3.11`, `node20`, `go1.21`)
- Function version in `.fission/config.yaml` (semantic versioning: `1.2.3`)
- Dependency versions in `requirements.txt`, `package.json`, `go.mod`

Renovate manages automated version bumps via PRs.

### 4. Always Test Before Committing

Every function change must pass tests locally:

```bash
# Test a specific function
task local-build-hello-world

# This builds the function, installs dependencies, and runs tests
```

Never commit code that fails local tests. GitHub Actions will reject builds with failing tests.

### 5. Validate Function Configuration

```bash
# Check .fission/config.yaml is valid YAML
yq eval .fission/config.yaml

# Verify function structure exists
test -d .fission && test -f .fission/config.yaml && test -f .fission/runtime.txt
```

---

## Repository Structure

```
functions/
    .github/
        actions/
            function-options/       Extracts function metadata from .fission/config.yaml
            function-tests/         Runs function tests based on runtime
            function-versions/      Handles semantic versioning
            release-tag/            Generates release tags (calver format)
        workflows/
            function-builder.yaml    Builds, tests, and packages functions
            release.yaml            Detects changed functions and triggers builds
    functions/
        <function-name>/           One directory per function
            .fission/
                config.yaml       Function metadata (environment, runtime, version, source)
                runtime.txt       Specific runtime version (python311, node20, go1.21)
            tests/                Unit tests for the function
            handler.py            Function handler code (Python example)
            requirements.txt       Python dependencies (if applicable)
            requirements-test.txt  Test dependencies (if applicable)
            package.json          Node.js dependencies (if applicable)
            go.mod               Go module dependencies (if applicable)
    include/                    Shared files included in all functions during build
    Taskfile.yaml               Task runner for local builds and remote CI triggers
    README.md                  User-facing documentation
    .gitignore
    .editorconfig
    LICENSE
```

---

## Function Structure Pattern

Every function follows this pattern:

```
functions/<function-name>/
    .fission/
        config.yaml              REQUIRED — function metadata
        runtime.txt              REQUIRED — exact runtime version
    tests/                     REQUIRED — unit tests
    handler.py                 Example: Python entry point
    requirements.txt           Python: production dependencies
    requirements-test.txt      Python: test dependencies
    pytest.ini                Python: pytest configuration
    handler.js                Example: Node.js entry point
    package.json              Node.js: dependencies and scripts
    handler.go                Example: Go entry point
    go.mod                   Go: module definition
    go.sum                   Go: locked dependencies
```

### Required Files

Every function MUST have:

1. **`.fission/config.yaml`** — Function metadata
   ```yaml
   environment: python          # Fission environment name
   runtime: python311          # Specific runtime version
   version: "1.0.0"          # Semantic version of the function
   source: https://github.com/example/functions
   ```

2. **`.fission/runtime.txt`** — Runtime specification
   - Python: `python311`, `python312`, `python313`
   - Node.js: `node20`, `node21`, `node22`
   - Go: `go1.21`, `go1.22`, `go1.23`

3. **`tests/` directory** — At least one test file
   - Python: `tests/test_handler.py`
   - Node.js: `tests/handler.test.js`
   - Go: `handler_test.go`

### Function Entry Points

#### Python Entry Point

```python
# handler.py
def handler(event):
    """
    Fission function handler

    Args:
        event: Dictionary containing request/event data

    Returns:
        Dictionary with response data (auto-converted to HTTP response)
    """
    name = event.get('name', 'World')
    return {
        'message': f'Hello, {name}!',
        'status': 'success'
    }
```

**Fission expects the entry point to be defined in `.fission/config.yaml` or specified at function creation time.**

#### Node.js Entry Point

```javascript
// handler.js
module.exports = async function(event) {
    const name = event.name || 'World';
    return {
        message: `Hello, ${name}!`,
        status: 'success'
    };
};
```

#### Go Entry Point

```go
// handler.go
package main

type Event struct {
    Name string `json:"name"`
}

type Response struct {
    Message string `json:"message"`
    Status  string `json:"status"`
}

func Handler(event Event) Response {
    name := event.Name
    if name == "" {
        name = "World"
    }
    return Response{
        Message: fmt.Sprintf("Hello, %s!", name),
        Status:  "success",
    }
}
```

---

## GitHub Actions Workflows

### Workflow: Release

Triggered by:
- Push to `main` branch with changes in `functions/` directory
- Manual workflow_dispatch with specific function name

Process:
1. **Prepare job** — Detects changed functions
2. **Changed job** — Builds list of functions to build
3. **Build job** — Calls function-builder workflow for each function (parallel, max 4 concurrent)
4. **Status job** — Fails if any build fails

```bash
# Manual trigger with release flag
gh workflow run release.yaml -f function=hello-world -f release=true
```

### Workflow: Function Builder

Called by release workflow for each function.

Jobs:
1. **Prepare job**
   - Checks if function directory exists
   - Extracts metadata from `.fission/config.yaml`
   - Resolves semantic version from upstream version

2. **Build job**
   - Sets up runtime environment (Python, Node.js, or Go)
   - Installs dependencies to `.fission/deps/`
   - Copies `include/` directory contents to function
   - Packages function as zip file: `<function>-<version>.zip`
   - Runs tests (unless `release=true`)

3. **Release job** (if `release=true`)
   - Generates release tag (calver format: `YYYY.MM.PATCH`)
   - Creates GitHub release with function package as asset
   - Includes function metadata in release notes

---

## Runtimes and Dependencies

### Python Runtime

**Supported versions:** `python311`, `python312`, `python313`

**Structure:**
```
functions/<function>/
    handler.py              Main function code
    requirements.txt        Production dependencies
    requirements-test.txt   Test dependencies (pytest)
    pytest.ini            Pytest configuration
    tests/
        test_handler.py     Unit tests
```

**requirements.txt example:**
```txt
requests==2.31.0
pydantic==2.5.0
boto3==1.34.0
```

**Local testing:**
```bash
# The local-build task installs dependencies and runs tests
task local-build-<function>
```

**Fission deployment:**
```bash
fission function create --name myfunction \
  --env python \
  --buildcmd "pip install -r requirements.txt -t ." \
  --src myfunction-1.0.0.zip \
  --entrypoint handler.handler
```

### Node.js Runtime

**Supported versions:** `node20`, `node21`, `node22`

**Structure:**
```
functions/<function>/
    handler.js              Main function code
    package.json            Dependencies and scripts
    tests/
        handler.test.js      Unit tests
```

**package.json example:**
```json
{
  "name": "myfunction",
  "version": "1.0.0",
  "dependencies": {
    "axios": "^1.6.0",
    "lodash": "^4.17.21"
  },
  "devDependencies": {
    "jest": "^29.7.0"
  },
  "scripts": {
    "test": "jest"
  }
}
```

**Local testing:**
```bash
task local-build-<function>
```

**Fission deployment:**
```bash
fission function create --name myfunction \
  --env nodejs \
  --buildcmd "npm install --production && cp -r node_modules/* ." \
  --src myfunction-1.0.0.zip \
  --entrypoint handler.Handler
```

### Go Runtime

**Supported versions:** `go1.21`, `go1.22`, `go1.23`

**Structure:**
```
functions/<function>/
    handler.go              Main function code
    go.mod                 Module definition
    go.sum                 Locked dependencies
    handler_test.go         Unit tests (can be in package or tests/ subdirectory)
```

**go.mod example:**
```go
module github.com/example/myfunction

go 1.21

require (
    github.com/aws/aws-lambda-go v1.13.3
    github.com/google/uuid v1.6.0
)
```

**Local testing:**
```bash
task local-build-<function>
```

**Fission deployment:**
```bash
fission function create --name myfunction \
  --env go \
  --buildcmd "go mod download -modcacherw" \
  --src myfunction-1.0.0.zip \
  --entrypoint Handler
```

---

## Common Operational Tasks

### Adding a New Function

1. **Create function directory:**
   ```bash
   mkdir -p functions/my-new-function/.fission
   mkdir -p functions/my-new-function/tests
   ```

2. **Create required metadata files:**
   ```bash
   # .fission/config.yaml
   cat > functions/my-new-function/.fission/config.yaml << EOF
   environment: python
   runtime: python311
   version: "1.0.0"
   source: https://github.com/example/functions
   EOF

   # .fission/runtime.txt
   echo "python3.11" > functions/my-new-function/.fission/runtime.txt
   ```

3. **Write function handler code:**
   - Create `handler.py`, `handler.js`, or `handler.go`
   - Implement the handler function following your runtime's pattern

4. **Add dependencies:**
   - Python: Create `requirements.txt` and `requirements-test.txt`
   - Node.js: Create `package.json` with dependencies and test scripts
   - Go: Run `go mod init` and add dependencies

5. **Write tests:**
   - Add at least one test file in `tests/`
   - Test the happy path and edge cases

6. **Test locally:**
   ```bash
   task local-build-my-new-function
   ```

7. **Commit and push:**
   ```bash
   git add functions/my-new-function
   git commit -m "Add new function: my-new-function"
   git push
   ```

8. **GitHub Actions will:**
   - Detect the new function
   - Build and test it
   - On successful push to `main`, create a release

### Updating a Function

1. **Modify function code or dependencies:**
   - Edit handler file
   - Update `requirements.txt`, `package.json`, or `go.mod`
   - Update tests

2. **Update version:**
   ```bash
   # Increment version in .fission/config.yaml
   # Follow semantic versioning: MAJOR.MINOR.PATCH
   # 1.0.0 -> 1.0.1 (bugfix)
   # 1.0.0 -> 1.1.0 (feature)
   # 1.0.0 -> 2.0.0 (breaking change)
   ```

3. **Test locally:**
   ```bash
   task local-build-<function>
   ```

4. **Commit and push:**
   ```bash
   git add functions/<function>
   git commit -m "Update <function> to 1.0.1"
   git push
   ```

### Releasing a Function

**Releases are created automatically when:**
- A new function is added and pushed to `main`
- A function is updated and pushed to `main`
- Manual workflow dispatch with `release=true`

**Release tag format:** CalVer (`YYYY.MM.PATCH`)

Example tags:
- `2025.4.0` — First release in April 2025
- `2025.4.1` — Second release in April 2025
- `2025.5.0` — First release in May 2025

**Download function package:**
```bash
# Download latest release
gh release download --repo <org>/functions --pattern <function>-*

# Download specific version
gh release download 2025.4.0 --pattern hello-world-*.zip
```

**Deploy to Fission:**
```bash
# Download package
gh release download 2025.4.0 --pattern hello-world-2025.4.0.zip

# Create or update function
fission function update --name hello-world \
  --env python \
  --src hello-world-2025.4.0.zip
```

### Debugging a Function

**Local testing:**
```bash
# This builds and runs tests with full output
task local-build-<function>

# Manually test with local Python
pip install -r functions/<function>/requirements.txt
python -c "from handler import handler; print(handler({'name': 'Test'}))"
```

**Remote debugging:**
```bash
# View GitHub Actions logs
gh run list --repo <org>/functions
gh run view <run-id>

# Watch function execution logs in Fission
fission function logs --name <function> --follow
```

**Fission function details:**
```bash
fission function get --name <function>
fission function list
```

### Managing Dependencies

**Python:**
```bash
# Add a new dependency
echo "new-package==1.0.0" >> functions/<function>/requirements.txt

# Update all dependencies
pip-compile requirements.in --upgrade

# Update test dependencies
pip-compile requirements-test.in --upgrade
```

**Node.js:**
```bash
# Add a new dependency
cd functions/<function>
npm install new-package@1.0.0

# Update all dependencies
npm update

# Audit for vulnerabilities
npm audit
```

**Go:**
```bash
# Add a new dependency
cd functions/<function>
go get github.com/example/package@v1.0.0

# Update all dependencies
go get -u ./...

# Tidy go.mod
go mod tidy
```

---

## Versioning Strategy

### CalVer (Calendar Versioning)

This repository uses CalVer for releases: `YYYY.MM.PATCH`

**Components:**
- `YYYY` — Year (4 digits)
- `MM` — Month (2 digits, 01-12)
- `PATCH` — Sequential release number within month

**Examples:**
- `2025.4.0` — April 2025, first release
- `2025.4.1` — April 2025, second release
- `2025.12.5` — December 2025, sixth release

**When PATCH increments:**
- Every function release
- Independent per function (multiple functions can release at different PATCH numbers)

### Semantic Versioning in Functions

While releases use CalVer, function versions in `.fission/config.yaml` can use semantic versioning (`MAJOR.MINOR.PATCH`):

- **MAJOR**: Incompatible API changes
- **MINOR**: Backwards-compatible functionality additions
- **PATCH**: Backwards-compatible bug fixes

The GitHub Actions `function-versions` action converts upstream versions to semantic format if valid, otherwise defaults to CalVer.

---

## Common Mistakes to Avoid

### 1. Forgetting to update version in .fission/config.yaml

**WRONG:**
```yaml
# Committing code changes without updating version
version: "1.0.0"  # still 1.0.0 after 5 commits
```

**CORRECT:**
```yaml
# Increment version for each release
version: "1.0.1"  # bugfix
version: "1.1.0"  # feature
version: "2.0.0"  # breaking change
```

### 2. Hardcoding secrets in function code

**WRONG:**
```python
def handler(event):
    api_key = "sk-1234567890abcdef"  # NEVER DO THIS
    # use api_key
```

**CORRECT:**
```python
import os

def handler(event):
    api_key = os.environ.get('API_KEY')  # Injected by Fission
    # use api_key
```

**Required env var must be documented in README or function comments.**

### 3. Not testing before pushing

GitHub Actions will fail if tests don't pass. Always run locally first:

```bash
task local-build-<function>
```

### 4. Using wrong runtime version

**WRONG:**
```text
# runtime.txt
python3.9  # Not supported
node18        # Not supported
```

**CORRECT:**
```text
# runtime.txt
python311  # Supported
node20       # Supported
```

Check supported versions in this document's "Runtimes and Dependencies" section.

### 5. Missing tests directory

Every function must have tests. Even a simple test is better than none:

**Python:**
```python
def test_handler_returns_message():
    from handler import handler
    result = handler({'name': 'Test'})
    assert result['status'] == 'success'
```

**Node.js:**
```javascript
test('handler returns success', () => {
    const handler = require('./handler');
    const result = handler({name: 'Test'});
    expect(result.status).toBe('success');
});
```

### 6. Committing build artifacts

The `.gitignore` excludes function zip files, but verify:

```bash
# Check no zip files are staged
git status

# If zip files appear, add them to .gitignore
echo "*.zip" >> .gitignore
```

### 7. Using mutable release tags

Never update an existing release. Always create a new release with a new tag.

**WRONG:**
- Editing release notes on existing `2025.4.0` tag
- Re-uploading new zip to existing release

**CORRECT:**
- Create new release `2025.4.1` with new zip
- Deprecate old release in notes if needed

---

## Function Deployment to Fission

### Prerequisites

1. **Fission CLI installed:**
   ```bash
   # Install via Go
   go install github.com/fission/fission/v2/cmd/fission@latest

   # Or via kubectl (recommended)
   kubectl create -k github.com/fission/fission
   ```

2. **Fission environment configured:**
   ```bash
   # List available environments
   fission env list

   # Create an environment if needed
   fission env create --name python --builder fission/python-builder
   fission env create --name nodejs --builder fission/node-env
   fission env create --name go --builder fission/go-env
   ```

3. **Function package downloaded:**
   ```bash
   gh release download --pattern <function>-*.zip
   ```

### Creating a Function

```bash
# Python function
fission function create --name hello-world \
  --env python \
  --src hello-world-2025.4.0.zip \
  --entrypoint handler.handler \
  --minscale 1 \
  --maxscale 5

# Node.js function
fission function create --name my-node-fn \
  --env nodejs \
  --src my-node-fn-2025.4.0.zip \
  --entrypoint handler.Handler \
  --minscale 1 \
  --maxscale 5

# Go function
fission function create --name my-go-fn \
  --env go \
  --src my-go-fn-2025.4.0.zip \
  --entrypoint Handler \
  --minscale 1 \
  --maxscale 5
```

### Updating a Function

```bash
# Update with new package
fission function update --name hello-world \
  --src hello-world-2025.4.1.zip
```

### Exposing a Function with HTTP Route

```bash
# Create an HTTP route
fission route create --name hello-world \
  --method GET \
  --url /hello \
  --function hello-world

# Or create with specific path
fission route create --name hello-world-alias \
  --method GET \
  --url /hello/{name} \
  --function hello-world
```

### Adding Environment Variables

```bash
# Add env vars at creation time
fission function create --name myfunction \
  --env python \
  --src myfunction-2025.4.0.zip \
  --entrypoint handler.handler \
  --envvar API_KEY=your-key-here \
  --envvar DEBUG=true

# Or create a ConfigMap and reference it
kubectl create configmap myfunction-config \
  --from-literal=API_URL=https://api.example.com

# Reference ConfigMap in function
fission function update --name myfunction \
  --configmap myfunction-config
```

### Monitoring Function Logs

```bash
# Follow logs for a specific function
fission function logs --name hello-world --follow

# View recent logs
fission function logs --name hello-world
```

### Deleting a Function

```bash
# Delete function (also removes associated routes)
fission function delete --name hello-world
```

---

## Task Runner Reference

### Available Tasks

```bash
# List all available tasks
task

# Initialize project (creates .bin directory)
task init

# Build and test a function locally
task local-build-<function>       # e.g., task local-build-hello-world

# Trigger remote build via GitHub Actions
task remote-build-<function>        # e.g., task remote-build-hello-world
task remote-build-<function> release=true
```

### Task: local-build-<function>

This task:
1. Creates a temporary working directory
2. Copies `include/` directory contents
3. Copies function directory contents
4. Installs dependencies based on runtime:
   - Python: `pip install --requirement requirements.txt --target .fission/deps`
   - Node.js: `npm install --production && cp -r node_modules/* .fission/deps/`
   - Go: `go mod download -modcacherw`
5. Runs tests
6. Packages function as zip file
7. Cleans up temporary directory

Output: `<function>-<version>.zip` in repository root

### Task: remote-build-<function>

This task:
1. Validates GitHub authentication
2. Triggers `release.yaml` workflow via `gh` CLI
3. Optionally sets `release=true` flag

---

## GitHub Actions Secrets

### Required Secrets for Releases

The following GitHub repository secrets must be configured for automated releases:

| Secret | Purpose |
|---|---|
| `BOT_APP_ID` | GitHub App ID for creating releases (automated) |
| `BOT_APP_PRIVATE_KEY` | Private key for GitHub App authentication |

**If these secrets are not configured:**
- Functions will still build and test
- Manual releases will need to be created via GitHub UI
- Automatic release creation will be skipped

### Optional Secrets

| Secret | Purpose |
|---|---|
| `DISCORD_WEBHOOK` | Discord webhook for build failure notifications |

---

## Testing Strategy

### Multi-Language Hybrid Approach

This repository uses a **hybrid testing strategy** to support multiple languages (Python, TypeScript, Go) while maintaining consistency and scalability:

### Standardized Structure (All Functions)

Every function follows the same directory structure:
```
functions/<function-name>/
├── .fission/
│   ├── config.yaml       # Function metadata
│   └── runtime.txt       # Runtime version
├── tests/                # Function tests
├── handler.py            # Python example
├── handler.ts            # TypeScript/Node.js example
├── handler.go             # Go example
├── requirements.txt      # Python dependencies
├── package.json          # Node.js dependencies
└── go.mod               # Go module dependencies
```

### Local Testing (All Languages)

```bash
# Test any function (Python, TypeScript, Go)
task local-build-<function>
```

The Taskfile automatically:
- Detects runtime from `.fission/runtime.txt`
- Installs dependencies (pip/npm/go mod)
- Runs language-appropriate tests
- Packages function as zip file

### CI/CD Validation (All Functions)

The existing `release.yaml` workflow provides:
- Automatic building and testing on push to main
- Parallel execution (up to 4 concurrent builds)
- Validation that tests pass before releases
- Support for all languages (Python, TypeScript, Go)

**Every function must pass tests before being released.**

### Language-Specific Testing

Functions choose their testing framework idiomatic to each language:
- **Python**: Uses pytest (standard, well-known)
- **TypeScript/Node.js**: Can use Jest or other test runners
- **Go**: Uses Go's built-in testing package

This approach provides:
- ✅ **Consistency**: Same structure across all languages
- ✅ **Flexibility**: Each function chooses appropriate testing approach
- ✅ **Scalability**: Taskfile + GitHub Actions supports 10-50+ functions
- ✅ **Quality**: Tests must pass before any release

### Unit Tests

Every function must have unit tests covering:
- Happy path (normal operation)
- Edge cases (empty input, null values)
- Error handling (invalid input, missing fields)

**Python (pytest):**
```python
# tests/test_handler.py
def test_handler_with_name():
    from handler import handler
    result = handler({'name': 'Alice'})
    assert result['message'] == 'Hello, Alice!'

def test_handler_without_name():
    from handler import handler
    result = handler({})
    assert result['message'] == 'Hello, World!'
```

**Node.js (Jest):**
```javascript
// tests/handler.test.js
describe('handler', () => {
    test('returns message with name', () => {
        const handler = require('../handler');
        const result = handler({name: 'Alice'});
        expect(result.message).toBe('Hello, Alice!');
    });

    test('defaults to World when no name', () => {
        const handler = require('../handler');
        const result = handler({});
        expect(result.message).toBe('Hello, World!');
    });
});
```

**Go (testing):**
```go
// handler_test.go
func TestHandler(t *testing.T) {
    tests := []struct {
        name     string
        event    Event
        expected Response
    }{
        {
            name:  "with name",
            event: Event{Name: "Alice"},
            expected: Response{Message: "Hello, Alice!", Status: "success"},
        },
        {
            name:  "without name",
            event: Event{},
            expected: Response{Message: "Hello, World!", Status: "success"},
        },
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            result := Handler(tt.event)
            if result != tt.expected {
                t.Errorf("Handler() = %v, want %v", result, tt.expected)
            }
        })
    }
}
```

### Integration Tests

Integration tests should verify:
- Function execution end-to-end
- Environment variable handling
- External service integration (if applicable)
- Performance characteristics (response time, memory usage)

Integration tests can be added to the `tests/` directory or run separately in CI.

---

## Best Practices

### 1. Keep Functions Small and Focused

Each function should do one thing well. Break large functions into smaller, composable functions.

### 2. Use Idempotent Operations

Functions should be idempotent — calling them multiple times with same input produces same result.

### 3. Handle Errors Gracefully

Always include error handling:
- Validate input
- Handle missing environment variables
- Return meaningful error messages
- Log errors for debugging

### 4. Add Logging

Use logging for debugging:
- Python: `logging` module
- Node.js: `console.log` or a logging library
- Go: `log` package or structured logging

```python
import logging
logger = logging.getLogger(__name__)

def handler(event):
    logger.info(f"Processing event: {event}")
    try:
        # function logic
    except Exception as e:
        logger.error(f"Error: {e}")
        raise
```

### 5. Document Dependencies

Keep `requirements.txt`, `package.json`, and `go.mod` clean and up-to-date:
- Remove unused dependencies
- Pin exact versions
- Document purpose of each dependency in comments

### 6. Use Type Hints (Python) or TypeScript (Node.js)

Type hints improve code quality and catch errors early:

```python
from typing import Dict, Any

def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handler function with type hints."""
    return {'status': 'success'}
```

### 7. Write Self-Documenting Code

Use clear variable names, function names, and add docstrings:

```python
def calculate_discount(price: float, discount_rate: float) -> float:
    """Calculate discounted price.

    Args:
        price: Original price in dollars
        discount_rate: Discount rate (0.0 to 1.0)

    Returns:
        Discounted price in dollars
    """
    return price * (1 - discount_rate)
```

---

## Troubleshooting

### Function Build Fails in GitHub Actions

**Check:**
1. Runtime version matches supported versions
2. Dependencies can be installed
3. Tests pass locally: `task local-build-<function>`
4. Function structure is valid: required files exist

**Debug:**
```bash
# View workflow run logs
gh run view <run-id> --log-failed
```

### Function Fails When Deployed

**Check:**
1. Entry point is correct in Fission command
2. Environment variables are set
3. Dependencies are properly packaged in zip
4. Function code is compatible with Fission runtime

**Debug:**
```bash
# View function logs
fission function logs --name <function> --follow

# Get function details
fission function get --name <function>

# Describe function pod
kubectl describe pod <pod-name> -n <function-namespace>
```

### Dependencies Not Found

**Cause:** Dependencies not included in function package

**Fix:**
- Ensure dependencies are installed to `.fission/deps/` during build
- Check build command in `function-builder.yaml`
- Verify dependencies are not in `.gitignore`

### Function Times Out

**Cause:** Function execution exceeds Fission timeout

**Fix:**
1. Optimize function code
2. Increase timeout in Fission:
   ```bash
   fission function update --name <function> --executetimeout 300s
   ```

### Function Returns 500 Error

**Cause:** Unhandled exception or error in handler

**Fix:**
1. Add comprehensive error handling
2. Check function logs for stack traces
3. Validate function return format

---

## Additional Resources

- [Fission Documentation](https://fission.io/docs/)
- [Fission GitHub Repository](https://github.com/fission/fission)
- [Python Packaging Guide](https://packaging.python.org/tutorials/packaging-projects/)
- [Node.js Best Practices](https://nodejs.org/en/docs/guides/best-practices-security/)
- [Go Modules Reference](https://go.dev/ref/mod)

---

## LLM-Assisted Development Workflow

When working with an LLM on this repository:

1. **LLM writes code and configuration** — Function handlers, tests, metadata files
2. **You run local tests** — `task local-build-<function>`
3. **LLM fixes any test failures** — Based on your test output
4. **You commit and push** — Only after all tests pass
5. **GitHub Actions builds and releases** — Automated CI/CD pipeline

**Key principles:**
- LLM never sees real secrets
- LLM provides structure, you provide values
- Local testing is mandatory before commits
- GitHub Actions validates everything on push
- LLM follows repository patterns for consistent structure across languages
