from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.connection import manager
from app.api.monitor import monitor
from app.api.schemas import CancelTaskResponse, TaskRequest, TaskStartResponse
from app.api.task_manager import task_manager
from app.utils.path_utils import ensure_session_dir


app = FastAPI(title="Glodex Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint for local and container probes."""
    return {"status": "ok"}


@app.post("/api/task", response_model=TaskStartResponse)
async def run_task(request: TaskRequest) -> TaskStartResponse:
    """Start an AgentLoop in the background and immediately return thread_id."""
    thread_id = await task_manager.start_task(
        query=request.query,
        thread_id=request.thread_id,
        user_id=request.user_id,
    )
    return TaskStartResponse(status="started", thread_id=thread_id)


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str) -> None:
    """Open a long-lived WebSocket for AGUI monitor events."""
    await manager.connect(websocket, thread_id)
    session_dir = ensure_session_dir(thread_id)
    await monitor.report_session_created(thread_id=thread_id, session_dir=str(session_dir))

    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
            elif message == "cancel_task":
                try:
                    await task_manager.cancel_task(thread_id)
                    await monitor.report_task_cancelled(thread_id=thread_id)
                except KeyError:
                    await monitor.report_error(
                        "not_found",
                        "任务不存在或已结束",
                        thread_id=thread_id,
                    )
            else:
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, thread_id)


@app.post("/api/task/{thread_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(thread_id: str) -> CancelTaskResponse:
    """Cancel a running AgentLoop task."""
    try:
        await task_manager.cancel_task(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已结束") from exc
    return CancelTaskResponse(status="cancelled", thread_id=thread_id)
