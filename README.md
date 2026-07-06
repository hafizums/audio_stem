## Audio Stem

Frappe app for AI vocal/instrumental separation using WaveSpeed.

## Milestone 1

Internal MVP implemented:

- Audio Separation Settings
- Audio Separation Job
- WaveSpeed backend integration
- Background worker processing
- Start Separation button on the job form
- Vocal and instrumental output URL storage
- Basic tests

## Milestone 2

User-facing Desk page at `/app/audio-vocal-remover`:

- Upload audio and create a job
- Start separation and poll job status
- Preview original, vocal, and instrumental outputs
- Download completed stems
- View recent jobs for the current user

### Manual test (Milestone 2)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
bench --site <your-site> clear-cache
bench build --app audio_stem
bench restart
```

1. Log in to Frappe Desk as a system user.
2. Open **Audio Vocal Remover** from the Audio Stem module (or go to `/app/audio-vocal-remover`).
3. Confirm **Audio Separation Settings** has `enabled` checked and a valid WaveSpeed API key saved.
4. Click **Upload Audio File** and choose an MP3 (or other supported audio file).
5. Confirm a job is created and the cost/duration section updates (or shows the fallback message).
6. Click **Start Separation**.
7. Confirm status moves through `Queued` → `Uploading` → `Processing` → `Completed`.
8. Confirm HTML5 audio players appear for outputs when URLs are available.
9. Use **Download Vocal** and **Download Instrumental** when completed.
10. Confirm the **Recent Jobs** table lists your job and clicking a row reloads it.
11. Log in as another user and confirm you cannot load the first user's job via the API/page flow.

### Run tests

```bash
bench --site <your-site> run-tests --app audio_stem
```

#### License

MIT
