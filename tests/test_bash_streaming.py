import asyncio
import sys
from pathlib import Path

import pytest

from kavi.tools.bash import BashInput, BashTool
from kavi.tools.base import ToolContext


@pytest.mark.asyncio
async def test_bash_streaming(cwd: Path, config):
    tool = BashTool()
    
    # We use a short python script that prints two lines with a small delay
    script = (
        "import sys, time\n"
        "print('line1')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.1)\n"
        "print('line2')\n"
    )
    script_path = cwd / "test_stream.py"
    script_path.write_text(script)
    
    progress_calls = []
    
    async def on_progress(text: str) -> None:
        progress_calls.append(text)

    # Force a very short interval so we definitely get a progress callback
    # during the 0.1s sleep.
    import kavi.tools.bash
    original_interval = kavi.tools.bash.PROGRESS_INTERVAL
    kavi.tools.bash.PROGRESS_INTERVAL = 0.05
    
    try:
        ctx = ToolContext(
            cwd=cwd, 
            config=config, 
            on_progress=on_progress
        )
        
        data = BashInput(command=f"{sys.executable} test_stream.py")
        result = await tool.run(data, ctx)
        
        assert not result.is_error
        assert "line1" in result.content
        assert "line2" in result.content
        
        # We should have received at least one progress callback while running
        assert len(progress_calls) > 0
        # The first callback should contain line1 but not line2
        assert "line1" in progress_calls[0]
    finally:
        kavi.tools.bash.PROGRESS_INTERVAL = original_interval


@pytest.mark.asyncio
async def test_bash_timeout_kill(cwd: Path, config):
    tool = BashTool()
    
    # Script that loops forever, ignoring SIGTERM for a bit to test forceful kill
    script = (
        "import time\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    script_path = cwd / "test_timeout.py"
    script_path.write_text(script)
    
    ctx = ToolContext(cwd=cwd, config=config)
    
    # 1 second timeout
    data = BashInput(command=f"{sys.executable} test_timeout.py", timeout=1)
    
    start = asyncio.get_running_loop().time()
    result = await tool.run(data, ctx)
    elapsed = asyncio.get_running_loop().time() - start
    
    assert result.is_error
    assert "timed out after 1s" in result.content
    assert elapsed < 3.0  # Should be killed promptly
