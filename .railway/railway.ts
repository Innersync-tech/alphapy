import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
// alphapy-dev also deploys from Innersync-tech/alphapy, but it is paused (no
// deployment) and is deliberately NOT declared here: declaring it would make
// `railway config apply` update and redeploy it. Add it back only when it should run.
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

  return project("Alphapy", {
    variables: { managed: false },
    resources: [alphapy],
  });
});
