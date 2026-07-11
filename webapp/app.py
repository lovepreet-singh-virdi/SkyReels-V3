import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
RESULT_DIR = BASE_DIR / "result"
UPLOAD_DIR = APP_DIR / "uploads"
BACKEND_MODE = os.environ.get("SKYREELS_BACKEND_MODE", "native").strip().lower()
WSL_WORKDIR = os.environ.get("SKYREELS_WSL_WORKDIR", "").strip()

app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)

_jobs = {}
_jobs_lock = threading.Lock()


def _now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _job_snapshot(job):
    with _jobs_lock:
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "backend_mode": job.get("backend_mode"),
            "command": job.get("command", ""),
            "logs": job.get("logs", [])[-300:],
            "output_path": job.get("output_path"),
            "output_url": job.get("output_url"),
            "error": job.get("error"),
            "task_type": job.get("task_type"),
        }


def _create_job(task_type):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "task_type": task_type,
        "status": "queued",
        "created_at": _now_text(),
        "logs": [],
    }
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def _get_job(job_id):
    with _jobs_lock:
        return _jobs.get(job_id)


def _append_log(job, line):
    with _jobs_lock:
        job["logs"].append(line.rstrip("\n"))
        if len(job["logs"]) > 500:
            job["logs"] = job["logs"][-300:]


def _save_upload(file_storage, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        filename = f"upload_{uuid.uuid4().hex}"
    target_path = target_dir / filename
    file_storage.save(target_path)
    return target_path


def _split_csv(value):
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _collect_paths_from_uploads(files, target_dir):
    paths = []
    for file_storage in files:
        if file_storage and file_storage.filename:
            paths.append(str(_save_upload(file_storage, target_dir)))
    return paths


def _latest_result_file(task_type, started_at_epoch):
    task_dir = RESULT_DIR / task_type
    if not task_dir.exists():
        return None

    candidates = []
    for file_path in task_dir.glob("*.mp4"):
        try:
            if file_path.stat().st_mtime >= started_at_epoch - 1:
                candidates.append(file_path)
        except FileNotFoundError:
            continue

    if not candidates:
        candidates = list(task_dir.glob("*.mp4"))

    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


def _to_wsl_path(path_str):
    path = Path(path_str).resolve()
    drive = path.drive.rstrip(":").lower()
    if not drive:
        return path.as_posix()
    return f"/mnt/{drive}{path.as_posix()[2:]}"


def _build_native_command(command):
    return command


def _build_wsl_command(command):
    if not WSL_WORKDIR:
        raise ValueError(
            "SKYREELS_WSL_WORKDIR is not set. "
            "Set it to the repo path inside WSL, for example /mnt/d/Nitika/Projects/SkyReels-V3."
        )

    translated = []
    for idx, part in enumerate(command):
        if idx == 0 and part == sys.executable:
            translated.append("python3")
            continue
        if idx == 1 and str(part).endswith("generate_video.py"):
            translated.append(_to_wsl_path(str(BASE_DIR / "generate_video.py")))
            continue

        text = str(part)
        if len(text) >= 2 and text[1] == ":":
            translated.append(_to_wsl_path(text))
        else:
            translated.append(text)

    quoted = " ".join(shlex.quote(item) for item in translated)
    return ["wsl", "bash", "-lc", f"cd {shlex.quote(WSL_WORKDIR)} && {quoted}"]


def _build_command(form, upload_dir):
    task_type = form.get("task_type", "").strip()
    prompt = form.get("prompt", "").strip()
    duration = form.get("duration", "5").strip()
    resolution = form.get("resolution", "720P").strip()
    seed = form.get("seed", "42").strip()
    model_id = form.get("model_id", "").strip()
    use_usp = form.get("use_usp") == "on"
    offload = form.get("offload") == "on"
    low_vram = form.get("low_vram") == "on"
    nproc_per_node = form.get("nproc_per_node", "4").strip()

    if use_usp and low_vram:
        raise ValueError("use_usp and low_vram cannot be enabled together.")

    script_path = BASE_DIR / "generate_video.py"
    command = [sys.executable]
    if use_usp:
        command += [
            "-m",
            "torch.distributed.run",
            f"--nproc_per_node={int(nproc_per_node)}",
        ]

    command.append(str(script_path))
    command += [
        "--task_type",
        task_type,
        "--prompt",
        prompt,
        "--duration",
        str(int(duration)),
        "--seed",
        str(int(seed)),
        "--resolution",
        resolution,
    ]

    if model_id:
        command += ["--model_id", model_id]
    if offload:
        command.append("--offload")
    if low_vram:
        command.append("--low_vram")
    if use_usp:
        command.append("--use_usp")

    if task_type == "reference_to_video":
        ref_text = _split_csv(form.get("ref_imgs", ""))
        ref_files = _collect_paths_from_uploads(request.files.getlist("ref_img_files"), upload_dir)
        ref_imgs = ref_text + ref_files
        if not ref_imgs:
            raise ValueError("Please provide at least one reference image.")
        command += ["--ref_imgs", ",".join(ref_imgs)]
    elif task_type in {"single_shot_extension", "shot_switching_extension"}:
        input_video = form.get("input_video", "").strip()
        video_files = _collect_paths_from_uploads(request.files.getlist("input_video_file"), upload_dir)
        if video_files:
            input_video = video_files[0]
        if not input_video:
            raise ValueError("Please provide an input video.")
        command += ["--input_video", input_video]
    elif task_type == "talking_avatar":
        input_image = form.get("input_image", "").strip()
        input_audio = form.get("input_audio", "").strip()
        image_files = _collect_paths_from_uploads(request.files.getlist("input_image_file"), upload_dir)
        audio_files = _collect_paths_from_uploads(request.files.getlist("input_audio_file"), upload_dir)
        if image_files:
            input_image = image_files[0]
        if audio_files:
            input_audio = audio_files[0]
        if not input_image:
            raise ValueError("Please provide a portrait image.")
        if not input_audio:
            raise ValueError("Please provide a driving audio file.")
        command += ["--input_image", input_image, "--input_audio", input_audio]
    else:
        raise ValueError(f"Unsupported task type: {task_type}")

    return command


def _run_job(job_id, command):
    job = _get_job(job_id)
    if job is None:
        return

    started_at = time.time()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    with _jobs_lock:
        job["status"] = "running"
        job["started_at"] = _now_text()
        job["started_at_epoch"] = started_at
        job["command"] = subprocess.list2cmdline(command)
        job["backend_mode"] = BACKEND_MODE

    try:
        runtime_command = _build_wsl_command(command) if BACKEND_MODE == "wsl" else _build_native_command(command)
        proc = subprocess.Popen(
            runtime_command,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )

        if proc.stdout is not None:
            for line in proc.stdout:
                _append_log(job, line)

        return_code = proc.wait()
        _append_log(job, f"[{_now_text()}] Process exited with code {return_code}")

        if return_code != 0:
            with _jobs_lock:
                job["status"] = "failed"
                job["error"] = f"Process exited with code {return_code}"
                job["finished_at"] = _now_text()
            return

        output_file = _latest_result_file(job["task_type"], started_at)
        output_path = str(output_file) if output_file else None
        output_url = None
        if output_file:
            rel_path = output_file.relative_to(BASE_DIR).as_posix()
            output_url = f"/artifacts/{rel_path}"

        with _jobs_lock:
            job["status"] = "succeeded"
            job["finished_at"] = _now_text()
            job["output_path"] = output_path
            job["output_url"] = output_url
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = _now_text()
        _append_log(job, f"[{_now_text()}] ERROR: {exc}")


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/run")
def api_run():
    task_type = request.form.get("task_type", "").strip()
    if not task_type:
        return jsonify({"error": "task_type is required"}), 400

    job = _create_job(task_type)
    upload_dir = UPLOAD_DIR / job["job_id"]

    try:
        command = _build_command(request.form, upload_dir)
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = _now_text()
        return jsonify({"error": str(exc), "job_id": job["job_id"]}), 400

    thread = threading.Thread(target=_run_job, args=(job["job_id"], command), daemon=True)
    thread.start()
    return jsonify(
        {
            "job_id": job["job_id"],
            "status": job["status"],
            "command": subprocess.list2cmdline(command),
            "backend_mode": BACKEND_MODE,
        }
    )


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    job = _get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(_job_snapshot(job))


@app.get("/artifacts/<path:relative_path>")
def artifacts(relative_path):
    file_path = (BASE_DIR / relative_path).resolve()
    try:
        file_path.relative_to(BASE_DIR.resolve())
    except ValueError:
        abort(403)

    if not file_path.exists():
        abort(404)

    return send_from_directory(file_path.parent, file_path.name, as_attachment=False)


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=7860, debug=True, threaded=True)
