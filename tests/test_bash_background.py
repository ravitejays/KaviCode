import asyncio
import sys
from pathlib import Path

import pytest

from kavi.tools.background import kill_task, list_tasks
from kavi.tools.bash import BashInput, BashTool
from kavi.tools.base import ToolContext


@pytest.mark.asyncio
async def test_bash_run_in_background(cwd: Path, config):
    tool = BashTool()
    notification_queue = asyncio.Queue()
    ctx = ToolContext(
        cwd=cwd, 
        config=config, 
        extras={"notification_queue": notification_queue}
    )
    
    # Fast-exiting script
    script = "print('hello from background')"
    script_path = cwd / "test_bg.py"
    script_path.write_text(script)
    
    data = BashInput(
        command=f"{sys.executable} test_bg.py", 
        run_in_background=True
    )
    result = await tool.run(data, ctx)
    
    # Run should return immediately with the task ID
    assert not result.is_error
    assert "Background task started" in result.content
    assert "Task ID" in result.content
    
    # Wait for the watcher to enqueue completion
    try:
        msg = await asyncio.wait_for(notification_queue.get(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("Background task completion not enqueued")
        
    assert "completed" in msg
    assert "Output log" in msg


@pytest.mark.asyncio
async def test_bash_background_kill(cwd: Path, config):
    tool = BashTool()
    notification_queue = asyncio.Queue()
    ctx = ToolContext(
        cwd=cwd, 
        config=config, 
        extras={"notification_queue": notification_queue}
    )
    
    # Long-running script
    script = (
        "import time\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    script_path = cwd / "test_bg_kill.py"
    script_path.write_text(script)
    
    data = BashInput(
        command=f"{sys.executable} test_bg_kill.py", 
        run_in_background=True
    )
    result = await tool.run(data, ctx)
    
    # Extract task ID from output string: "Task ID : <id>"
    import re
    match = re.search(r"Task ID\s*:\s*(\w+)", result.content)
    assert match is not None
    task_id = match.group(1)
    
    tasks_before = len(list_tasks())
    assert tasks_before > 0
    
    # Kill it
    killed = await kill_task(task_id)
    assert killed is True
    
    # The watcher should soon enqueue the failure (since we killed it)
    try:
        msg = await asyncio.wait_for(notification_queue.get(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("Background task completion not enqueued after kill")
        
    assert "failed" in msg
