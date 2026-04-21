# Matriarch Updater Function

Fission function to automatically download and update "I'll Be The Matriarch In This Life" manga chapters from Violet Scans.

## Functionality

This function implements a complete update workflow:
- Downloads chapters from Violet Scans
- Writes CBZ files to NFS scratch area
- Triggers Komga library scan via API
- Verifies successful import
- Cleans up scratch files after verification

## Deployment

### Prerequisites

1. **Fission Environment**: Create the `python3-manga-updater` environment with NFS mount
2. **Komga API Key**: Generate API key in Komga settings
3. **Environment Variables**: Configure the following variables

### Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `SERIES_NAME` | No | Manga series name | `I'll Be The Matriarch In This Life` |
| `KOMGA_API_URL` | Yes | Komga API endpoint | `http://komga.media.svc.cluster.local:8080` |
| `KOMGA_API_KEY` | Yes | Komga API authentication key | `your-api-key-here` |
| `KOMGA_LIBRARY_ID` | No | Komga library ID to scan | `your-library-id` |
| `VIOLET_URL` | No | Violet Scans base URL | `https://violetscans.org/comics/ill-be-the-matriarch-in-this-life/` |
| `DRY_RUN` | No | Test mode without actual downloads | `false` |

### Local Testing

```bash
task local-build-matriarch
```

### Remote Building

```bash
task remote-build-matriarch
```

### Deploy to Fission

```bash
# Create Fission environment with NFS mount
fission env create --name python3-manga-updater \
  --image python:3.11-slim \
  --builder fission/python-builder

# Create function
fission function create --name matriarch-updater \
  --env python3-manga-updater \
  --src matriarch-1.0.0.zip \
  --entrypoint handler.handler \
  --minscale 1 \
  --maxscale 3 \
  --envvar SERIES_NAME="I'll Be The Matriarch In This Life" \
  --envvar KOMGA_API_URL="http://komga.media.svc.cluster.local:8080" \
  --envvar KOMGA_API_KEY="your-api-key-here"
```

### Create HTTP Route (Optional)

```bash
fission route create --name matriarch-updater \
  --method POST \
  --url /matriarch/update \
  --function matriarch-updater
```

### Manual Testing

```bash
# Test with dry run
fission function test --name matriarch-updater --env '{"DRY_RUN": "true"}'

# Test actual execution
fission function test --name matriarch-updater
```

## Workflow

1. **Check Komga API** - Query existing books in series
2. **Check Violet Scans** - Determine latest available chapter
3. **Download Missing Chapters** - Download and create CBZ files
4. **Write to NFS Scratch** - Save CBZ files to scratch directory
5. **Trigger Komga Scan** - API call to trigger library analysis
6. **Verify Import** - Check books appear in Komga
7. **Cleanup Scratch** - Remove CBZ files after successful import

## Error Handling (MVP)

The function implements basic error handling:
- API call failures with logging
- File operation errors with logging
- Network timeout handling
- Validation of required environment variables
- Graceful degradation on partial failures
- Returns detailed error messages for troubleshooting

## Architecture

```
┌─────────────────┐
│  Fission       │
│  Function      │
│  (Python)      │
└──────┬──────────┘
       │
       │ NFS Mount: /mnt/scratch/matriarch
       │
┌──────▼──────────┐
│  NFS Share      │
│  Scratch Area   │
└──────┬──────────┘
       │
       │ Komga API Trigger
┌──────▼──────────┐
│  Komga API     │
└─────────────────┘
```

## Security

- No secrets in code - all credentials via environment variables
- API key authentication for Komga
- Minimal permissions principle
- NFS mount requires proper K8s RBAC

## Troubleshooting

### Function Fails to Start

1. Check environment variables are set
2. Verify NFS mount is accessible
3. Check Fission function logs: `fission function logs --name matriarch-updater --follow`

### Download Failures

1. Check Violet Scans site is accessible
2. Verify network connectivity from Fission pod
3. Check scratch directory write permissions

### Komga Import Failures

1. Verify API key is valid
2. Check Komga API endpoint is reachable
3. Verify library ID is correct

### NFS Mount Issues

1. Verify Fission environment PodSpec has correct volume configuration
2. Check NFS server connectivity
3. Verify mount path permissions

## Future Enhancements

- Add retry logic with exponential backoff
- Implement circuit breaker pattern for API calls
- Add detailed metrics and monitoring
- Support for multiple manga series
- Webhook notifications on new chapters