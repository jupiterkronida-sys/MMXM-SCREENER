"""Audit trail — tracks config, data hashes, and timestamps for reproducibility.

Every backtest records:
  - Timestamp
  - Config snapshot (from backtest_config.yaml)
  - Data hashes (from snapshot_manager)
  - Parameters used
  - Results summary
"""
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backtesting.reporting.metrics import MetricsReport


@dataclass
class AuditEntry:
    timestamp: str = ""
    pipeline_version: str = "1.0.0"
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    data_hashes: Dict[str, str] = field(default_factory=dict)
    training_date_range: Dict[str, str] = field(default_factory=dict)
    holdout_date_range: Dict[str, str] = field(default_factory=dict)
    strategy_params: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    cpcv_result: Dict[str, Any] = field(default_factory=dict)
    gate8_decision: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""


class AuditTrail:
    """Tracks reproducibility data for each backtest run."""

    def __init__(self, output_dir: Optional[Path] = None):
        if output_dir is None:
            output_dir = Path(__file__).resolve().parent.parent / "outputs" / "audit"
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._entries: List[AuditEntry] = []
        self._current: Optional[AuditEntry] = None

    def start_run(self, notes: str = "") -> None:
        """Start a new audit entry."""
        self._current = AuditEntry(
            timestamp=datetime.now().isoformat(),
            notes=notes,
        )
        self._load_config_snapshot()
        self._entries.append(self._current)

    def _load_config_snapshot(self) -> None:
        """Snapshot the current config."""
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
        if cfg_path.exists():
            import yaml
            with open(cfg_path) as f:
                self._current.config_snapshot = yaml.safe_load(f)

    def record_data_hashes(self, hashes: Dict[str, str]) -> None:
        if self._current:
            self._current.data_hashes = dict(hashes)

    def record_date_ranges(self, training: Dict[str, str],
                           holdout: Dict[str, str]) -> None:
        if self._current:
            self._current.training_date_range = dict(training)
            self._current.holdout_date_range = dict(holdout)

    def record_strategy_params(self, params: Dict[str, Any]) -> None:
        if self._current:
            self._current.strategy_params = dict(params)

    def record_metrics(self, metrics: MetricsReport) -> None:
        if self._current:
            self._current.metrics = asdict(metrics)

    def record_cpcv(self, cpcv_dict: Dict[str, Any]) -> None:
        if self._current:
            self._current.cpcv_result = dict(cpcv_dict)

    def record_gate8(self, gate_dict: Dict[str, Any]) -> None:
        if self._current:
            self._current.gate8_decision = dict(gate_dict)

    def save(self, filename: Optional[str] = None) -> Path:
        """Save all entries to a JSON file."""
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"audit_trail_{ts}.json"

        filepath = self.output_dir / filename
        data = [asdict(e) for e in self._entries]

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        return filepath

    @property
    def current(self) -> Optional[AuditEntry]:
        return self._current
