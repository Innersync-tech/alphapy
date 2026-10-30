import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
// Both alphapy (main) and alphapy-dev deploy from Innersync-tech/alphapy, so this
// one partial owns both services.
export const partial = "alphapy";
export default defineRailway(() => {
  const alphapy = service("alphapy", {
    source: github("Innersync-tech/alphapy", { branch: "main", checkSuites: false }),
    build: { buildEnvironment: "V3", builder: "Dockerfile" },
    start: "python3 bot.py",
    healthcheck: "/api/health",
    replicas: { "europe-west4-drams3a": 1 },
    domains: [{ domain: "alphapy.innersync.tech", port: 8000 }],
  });
  // alphapy-dev: source MUST stay declared (omitting it disconnects the repo).
  // The branch is pinned here: switching the dev branch in the dashboard will show
  // up as drift; change it in this file instead (or with `railway config apply`).
  const alphapyDev = service("alphapy-dev", {
    source: github("Innersync-tech/alphapy", { branch: "feat/home-guild-user-action-logs", checkSuites: false }),
    build: { buildEnvironment: "V3", builder: "Dockerfile" },
    start: "python3 bot.py",
    healthcheck: "/api/health",
    replicas: { "europe-west4-drams3a": 1 },
    networking: { privateNetworkEndpoint: "hopeful-alignment" },
  });

  return project("Alphapy", {
    variables: { managed: false },
    resources: [alphapy, alphapyDev],
  });
});
