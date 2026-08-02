import pytest

from kavi.messages import Usage
from kavi.cost.tracker import CostLimitExceeded, CostTracker, CostWarning


def test_cost_tracker_no_limits():
    tracker = CostTracker()
    warning = tracker.record("claude-sonnet-4", Usage(input_tokens=1000000, output_tokens=1000000))
    assert isinstance(warning, float)
    assert tracker.total_cost_usd == 18.0  # 3.0 + 15.0


def test_cost_tracker_warning():
    tracker = CostTracker(warn_cost_usd=1.0)
    
    # Small usage under threshold
    res = tracker.record("claude-sonnet-4", Usage(input_tokens=10000, output_tokens=10000))
    assert isinstance(res, float)
    
    # Push it over
    res = tracker.record("claude-sonnet-4", Usage(input_tokens=1000000, output_tokens=1000000))
    assert isinstance(res, CostWarning)
    
    # Further usage doesn't re-warn
    res = tracker.record("claude-sonnet-4", Usage(input_tokens=10000, output_tokens=10000))
    assert isinstance(res, float)


def test_cost_tracker_limit():
    tracker = CostTracker(max_cost_usd=2.0)
    
    # Under limit
    tracker.record("claude-sonnet-4", Usage(input_tokens=10000, output_tokens=10000))
    
    # Over limit
    with pytest.raises(CostLimitExceeded):
        tracker.record("claude-sonnet-4", Usage(input_tokens=1000000, output_tokens=1000000))
