# VStudy authentication-state experiment

This phase tests whether VStudy authentication can be reused in a fresh GitHub-hosted Chromium session. It does not add a schedule or change the existing monitor/VPS workflow.

## 1. Prepare the local environment

Use the project virtual environment and install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Generate a Fernet key into the current PowerShell process without printing it:

```powershell
$env:VSTUDY_STATE_KEY = (& .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
```

The key is required by both the exporter and the GitHub workflow. Do not put it in a file or commit it.

## 2. Create the encrypted state locally

Run the bootstrap utility:

```powershell
$stateFile = Join-Path $env:TEMP "vstudy-auth-state.enc"
.\.venv\Scripts\python.exe bootstrap_vstudy_auth.py --output $stateFile
```

A temporary Chrome profile opens. Complete the VStudy login manually using the installed browser, confirm that the dashboard is visible, and press ENTER in PowerShell. The utility exports only:

- Cookies whose domain is `saveetha.com` or a subdomain.
- `localStorage` and `sessionStorage` for the HTTPS VStudy origin.

It does not copy the Chrome profile, Google cookies, passwords, or unrelated browser data. The temporary profile is deleted when the utility exits. The output file is encrypted before it is written.

## 3. Add the two GitHub Secrets

The GitHub CLI must already be authenticated for the target repository. Keep the same PowerShell session so the key remains in its environment:

```powershell
$stateB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($stateFile))
gh secret set VSTUDY_STATE_KEY --body $env:VSTUDY_STATE_KEY
gh secret set VSTUDY_AUTH_STATE_B64 --body $stateB64
Remove-Item $stateFile -Force
```

Required secrets:

- `VSTUDY_STATE_KEY`: the Fernet key generated above.
- `VSTUDY_AUTH_STATE_B64`: base64 of the encrypted state file.

The state is stored only as an encrypted repository secret. It is never committed and the workflow does not upload an artifact or use GitHub Actions cache. The state file is expected to be small enough for the GitHub Actions secret size limit.

## 4. Start the test

Open the repository on GitHub, select **Actions**, choose **VStudy authentication-state experiment**, select **Run workflow**, and run it from a trusted branch. The workflow is `workflow_dispatch` only and uses `ubuntu-latest`; it does not use a self-hosted runner, VPS, or schedule.

A successful run includes output similar to:

```text
[PASS] Fresh Chromium profile recognized VStudy authentication
[PASS] Profile page opened
[PASS] View Details flow opened the detailed profile
[PASS] Existing scraper returned 17 course result(s)
[PASS] VStudy auth-state experiment completed at https://vstudy.saveetha.com/
```

The exact result count may change. Authentication must be recognized before the profile and scraper checks can pass.

## Failure meanings

- `VSTUDY_STATE_KEY is not configured`: the repository secret is missing.
- `VSTUDY_AUTH_STATE_B64 is not configured`: the encrypted state secret is missing.
- `Authentication state could not be decrypted`: the key and encrypted file do not match, or the state was corrupted.
- `Authentication state was not recognized by the fresh Chromium profile`: the saved state is expired, device-bound, incomplete, or VStudy/Google requires a new interactive login. Credentials in GitHub Secrets will not repair this OAuth session.
- Profile or `View Details` failure after authentication: the state was accepted, but the VStudy page flow or scraper assumptions changed.

If state reuse fails, create a fresh encrypted state locally and replace `VSTUDY_AUTH_STATE_B64`. Do not upload the plaintext state or the full browser profile.
