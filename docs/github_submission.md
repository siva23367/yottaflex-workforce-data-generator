# Publish to a public GitHub repository

The challenge portal requires a **public** GitHub repository URL. The project is prepared locally, but publishing requires your GitHub account and a repository you create.

1. Create an empty public repository on GitHub, for example `yottaflex-workforce-scheduler`. Do not initialize it with a README or license (this project already has a README).
2. From this project directory, run:

```bash
git init
git add .
git commit -m "Build deterministic workforce scheduling data generator"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/yottaflex-workforce-scheduler.git
git push -u origin main
```

3. Open the repository URL in a private/incognito browser window to confirm it is public and all files are visible.
4. Paste that repository URL into the YottaFlex submission portal. The portal states that submission is final and can be made only once, so verify the URL and repository first.

Do not submit the local ZIP download link; the portal asks for a public GitHub repository URL.
