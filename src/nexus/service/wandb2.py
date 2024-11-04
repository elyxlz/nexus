import wandb


def get_wandb_run_info(partial_run_id):
    api = wandb.Api()

    # Search for the run across all accessible projects
    runs = api.runs(f"*/*/{partial_run_id}")

    print(runs)

    if not runs:
        raise ValueError(f"Could not find any run with ID {partial_run_id}")

    if len(runs) > 1:
        print(f"Warning: Found multiple runs with ID {partial_run_id}. Using the first one.")

    run = runs[0]

    entity = run.entity
    project = run.project
    run_id = run.id
    run_url = run.url

    return entity, project, run_id, run_url


# Usage
try:
    entity, project, run_id, run_url = get_wandb_run_info("wj6wordt")
    print(f"Entity: {entity}")
    print(f"Project: {project}")
    print(f"Run ID: {run_id}")
    print(f"Run URL: {run_url}")
except ValueError as e:
    print(f"Error: {e}")
