\## Setup



This app reads your "Now Playing" from Spotify, so you'll create your own

free Spotify Developer app (takes 2 minutes).



1\. Go to https://developer.spotify.com/dashboard and log in

2\. Click \*\*Create app\*\*

3\. Fill in:

&#x20;  - App name: anything

&#x20;  - Redirect URI: `http://127.0.0.1:8888/callback`  \*(exact, no trailing slash)\*

&#x20;  - Check the \*\*Web API\*\* box

4\. Save → open the new app → \*\*Settings\*\* → copy your \*\*Client ID\*\*

5\. Run Lyric Display once. It'll open `config.json` for you.

6\. Paste your client ID:

```json

&#x20;  { "client\_id": "paste\_here" }

```

7\. Save the file and reopen the app. Browser opens, you log in, done.



\### Troubleshooting

\- \*\*"Invalid redirect URI"\*\*: step 3 doesn't match exactly. Trailing slashes count.

\- \*\*"User not registered"\*\*: in your dashboard, \*\*Settings → User Management\*\*, add your own Spotify email.

\- \*\*Exe won't launch / "missing DLL" error\*\*: Install the [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).



\### Controls

\-\*\*M\*\* to open menu.

\-\*\*F11\*\* to toggle full-screen.

\-\*\*Esc\*\* to exit.



