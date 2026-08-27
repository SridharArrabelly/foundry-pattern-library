# Pattern 5B hosted — workflow as an agent

The same explicit `WorkflowBuilder` graph used by
[`../run_workflow.py`](../run_workflow.py) is wrapped with `workflow.as_agent()` and
served through the Responses protocol.

## Run locally

```powershell
cd 05b-workflow-orchestration/hosted
copy src\workflow-orchestrator\.env.example src\workflow-orchestrator\.env
azd ai agent run
```

Invoke from a second terminal with a JSON user message:

```powershell
azd ai agent invoke --local '{"request_id":"CHG-1001","operation":"restart_service","environment":"test","estimated_users":20,"rollback_plan":"Restart the previously approved service revision."}'
```

## Deploy

```powershell
azd ext install microsoft.foundry
azd auth login --tenant-id <foundry-tenant-id>

$project = "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>"
azd ai agent init --no-prompt --project-id $project `
  --deploy-mode code --runtime python_3_13 --entry-point main.py `
  --src ./src/workflow-orchestrator `
  --agent-name workflow-orchestrator-hosted --force

azd deploy
```

The hosted endpoint does not persist local checkpoint files. Use a production checkpoint
provider such as Cosmos DB with managed identity/RBAC when cross-instance resume is
required.
