import pathlib

__all__ = ["find_wandb_run_by_nexus_id"]


def parse_wandb_file(file_path: str) -> tuple[str, str, str] | None:
    try:
        with open(file_path, "rb") as f:
            content = f.read()
            text = content.decode("utf-8", errors="ignore")
            lines = text.splitlines()

            for i, line in enumerate(lines):
                if "job" in line and i > 0:
                    entity = line.split('"')[0]
                    project = lines[i - 1].split("\x1a")[0].split("\x0f")[1]
                    run_id = file_path.split("-")[-1].split(".wandb")[0]

                    if all([run_id, project, entity]):
                        return entity, project, run_id

        return None

    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return None


def find_wandb_run_by_nexus_id(dirs: list[str], nexus_job_id: str) -> str:
    for root_dir in dirs:
        root_path = pathlib.Path(root_dir)
        for wandb_file in root_path.rglob("*.wandb"):
            try:
                with open(wandb_file, "rb") as f:
                    content = f.read(4096)
                    if nexus_job_id.encode("utf-8") in content:
                        result = parse_wandb_file(str(wandb_file))
                        if result:
                            entity, project, run_id = result
                            return f"https://wandb.ai/{entity}/{project}/{run_id}"
            except Exception as e:
                print(f"Error processing {wandb_file}: {e}")
    return ""


if __name__ == "__main__":
    wandb_url = find_wandb_run_by_nexus_id(["/home/elyx/Audiogen/model-factory/wandb"], "model")
    if wandb_url:
        print(f"Found wandb URL: {wandb_url}")
    else:
        print("nothing found")
