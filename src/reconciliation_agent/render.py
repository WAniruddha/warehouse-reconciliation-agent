"""Human-readable renderers for reconciliation reports."""

from __future__ import annotations

from .report import ReconciliationReport


def render_text_report(report: ReconciliationReport) -> str:
    """Render a concise terminal report for the live demonstration."""

    lines = [
        "=" * 72,
        "WAREHOUSE RECONCILIATION REPORT",
        "=" * 72,
        f"Simulation: {report.simulation_id}",
        f"Snapshot: {report.snapshot_id} (as of {report.snapshot_as_of.isoformat()})",
        f"Snapshot received: {report.snapshot_received_at.isoformat()}",
        f"Events processed: {report.events_processed_before_snapshot}",
        "",
        "INVENTORY DIVERGENCE AT SNAPSHOT",
        "-" * 72,
    ]
    for item in report.inventory_divergences:
        status = "DIVERGED" if item.diverged else "CONFIRMED"
        lines.append(f"[{status}] {item.sku}: {item.explanation}")

    lines.extend(["", "DECISION AUDIT", "-" * 72])
    for number, finding in enumerate(report.findings, start=1):
        lines.extend(
            [
                f"{number}. {finding.decision_id} / {finding.order_id} / {finding.sku}",
                f"   Original reasoning: {finding.original_trace.explanation}",
                f"   Reconciliation: {finding.audit_explanation}",
                f"   Reasoning gap: {finding.reasoning_gap}",
                f"   Verdict: {finding.verdict.value}",
                "   Human review: "
                + ("YES" if finding.requires_human_review else "NO"),
                "",
            ]
        )

    lines.extend(
        [
            "FINAL STATE",
            "-" * 72,
            f"Original projected ATP: {report.original_final_available_to_promise}",
            f"Candidate corrected ATP: {report.candidate_final_available_to_promise}",
            f"Promotion status: {report.promotion_status.value}",
            "",
            report.summary,
        ]
    )
    return "\n".join(lines)


def render_markdown_report(report: ReconciliationReport) -> str:
    """Render a standalone reviewer-friendly Markdown audit report."""

    lines = [
        "# Warehouse reconciliation report",
        "",
        f"- **Simulation:** `{report.simulation_id}`",
        f"- **Warehouse:** `{report.warehouse_id}`",
        f"- **Snapshot:** `{report.snapshot_id}`",
        f"- **Authoritative as of:** `{report.snapshot_as_of.isoformat()}`",
        f"- **Received at:** `{report.snapshot_received_at.isoformat()}`",
        f"- **Events processed before arrival:** {report.events_processed_before_snapshot}",
        f"- **Promotion status:** `{report.promotion_status.value}`",
        "",
        report.summary,
        "",
        "## Inventory divergence at the snapshot boundary",
        "",
        "| SKU | Simulation base | Expected inbound | Simulation projected | "
        "Warehouse | Difference | Status |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report.inventory_divergences:
        status = "Diverged" if item.diverged else "Confirmed"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.sku}`",
                    _display(item.simulation_base_available_to_promise),
                    _display(item.simulation_expected_inbound),
                    _display(item.simulation_projected_available_to_promise),
                    _display(item.warehouse_available_to_promise),
                    _display(item.warehouse_minus_simulation),
                    status,
                ]
            )
            + " |"
        )

    lines.extend(["", "## Decision audit", ""])
    for finding in report.findings:
        original = finding.original_trace
        corrected = finding.corrected_trace
        lines.extend(
            [
                f"### {finding.decision_id}: `{finding.verdict.value}`",
                "",
                f"**Original decision-time explanation:** {original.explanation}",
                "",
                f"**Warehouse audit:** {finding.audit_explanation}",
                "",
                f"**Reasoning gap:** {finding.reasoning_gap}",
                "",
                "| Field | Original | Corrected |",
                "|---|---:|---:|",
                f"| Base available-to-promise | {original.base_available_to_promise} | "
                f"{_display(corrected.base_available_to_promise if corrected else None)} |",
                f"| Expected inbound | {original.expected_inbound} | "
                f"{_display(corrected.expected_inbound if corrected else None)} |",
                f"| Decision availability | {original.decision_available_to_promise} | "
                f"{_display(corrected.decision_available_to_promise if corrected else None)} |",
                f"| Action | `{original.action.value}` | "
                f"{f'`{corrected.action.value}`' if corrected else 'Unknown'} |",
                "",
                f"- Evidence available originally: "
                f"{', '.join(f'`{item}`' for item in original.evidence_ids)}",
                f"- Assumptions used: "
                f"{_display_ids(original.assumption_event_ids)}",
                f"- Invalidated assumptions: "
                f"{_display_ids(finding.invalidated_assumption_event_ids)}",
                f"- Invalidated historical outputs: "
                f"{_display_ids(finding.invalidated_output_event_ids)}",
                f"- Human review required: "
                f"{'Yes' if finding.requires_human_review else 'No'}",
                "",
            ]
        )

    lines.extend(
        [
            "## Final projected state",
            "",
            "| SKU | Original ATP | Corrected candidate ATP |",
            "|---|---:|---:|",
        ]
    )
    for sku in sorted(report.original_final_available_to_promise):
        lines.append(
            f"| `{sku}` | {report.original_final_available_to_promise[sku]} | "
            f"{report.candidate_final_available_to_promise.get(sku, 'Unknown')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _display(value: object | None) -> str:
    return "Unknown" if value is None else str(value)


def _display_ids(values: tuple[str, ...]) -> str:
    if not values:
        return "None"
    return ", ".join(f"`{value}`" for value in values)
