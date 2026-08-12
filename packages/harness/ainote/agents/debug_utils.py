"""
Debug print utilities for consistent logging across the application.

This module provides formatted print functions for debugging HITL workflows,
task operations, and other agent activities.
"""

from typing import Any


# =============================================================================
# SECTION PRINTERS
# =============================================================================
def print_section(title: str) -> None:
    """Print a section header with clear separators."""
    print("\n" + "=" * 70)
    print(f"[DEBUG] {title}")
    print("=" * 70)


def print_subsection(title: str) -> None:
    """Print a subsection header."""
    print("-" * 70)
    print(f"[DEBUG] {title}")
    print("-" * 70)


def print_end_section() -> None:
    """Print section ending separator."""
    print("=" * 70 + "\n")


# =============================================================================
# TASK PRINTERS
# =============================================================================
def print_task_item(idx: int, key: str, task: dict[str, Any], action: str | None = None) -> None:
    """Print a single task item with consistent formatting.

    Args:
        idx: Item index (1-based)
        key: Task key/ID
        task: Task data dictionary
        action: Optional action type (insert/update/delete)
    """
    print(f"[DEBUG] [{idx}] key={key}")
    if action:
        print(f"[DEBUG]     action={action}")
    print(f"[DEBUG]     title={task.get('title')}")
    print(f"[DEBUG]     priority={task.get('priority')}")
    print(f"[DEBUG]     time={task.get('time')}")
    print("-" * 70)


def print_proposed_tasks(proposed: list[dict]) -> None:
    """Print proposed tasks for HITL debugging.

    Args:
        proposed: List of proposed task dictionaries with keys:
            - key: Task key
            - action: insert/update/delete
            - task: Task data dict
    """
    print_section("HITL_NODE: PROPOSED TASKS")
    if not proposed:
        print("[DEBUG] No proposed tasks found!")
        print_end_section()
        return

    print(f"[DEBUG] Total proposed: {len(proposed)}")
    for idx, p in enumerate(proposed, 1):
        task = p.get('task', {})
        print_task_item(idx, p.get('key'), task, p.get('action'))
    print_end_section()


def print_approval_result(approval: dict, rejected_keys: set, edited_tasks: dict) -> None:
    """Print approval result for HITL debugging.

    Args:
        approval: Approval response dict with 'approved' key
        rejected_keys: Set of rejected task keys
        edited_tasks: Dict of edited tasks {key: task_data}
    """
    print_section("HITL_NODE: APPROVAL RECEIVED")
    print(f"[DEBUG] approved={approval.get('approved')}")
    print(f"[DEBUG] rejected_keys={list(rejected_keys)}")
    print(f"[DEBUG] edited_tasks count={len(edited_tasks)}")

    if edited_tasks:
        print_subsection("Edited Tasks Details")
        for idx, (key, task) in enumerate(edited_tasks.items(), 1):
            print_task_item(idx, key, task)
    print_end_section()


def print_final_upserts(upserts: list[tuple[str, dict]]) -> None:
    """Print final upserts for HITL debugging.

    Args:
        upserts: List of (key, task_data) tuples to be upserted
    """
    print_section("HITL_NODE: FINAL UPSETS")
    if not upserts:
        print("[DEBUG] No upserts to apply")
        print_end_section()
        return

    for idx, (key, task) in enumerate(upserts, 1):
        print_task_item(idx, key, task)
    print_end_section()


# =============================================================================
# HITL SUMMARY BUILDER
# =============================================================================
def build_hitl_summary(
    proposed: list[dict],
    edited_tasks: dict[str, dict],
    rejected_keys: set[str],
    deleted_count: int,
    upserts_count: int,
    addToDingtalk: bool = False,
) -> str:
    """Build a detailed summary message for HITL approval results.

    Args:
        proposed: Original proposed tasks
        edited_tasks: Dict of edited tasks {key: task_data}
        rejected_keys: Set of rejected task keys
        deleted_count: Number of deleted tasks
        upserts_count: Number of upserted tasks

    Returns:
        Human-readable summary message
    """
    summary_parts = []

    # Track edited tasks
    if edited_tasks:
        for key, new_task in edited_tasks.items():
            original_task = next((p["task"] for p in proposed if p["key"] == key), None)

            if original_task:
                old_title = original_task.get("title", "Untitled")
                new_title = new_task.get("title", "Untitled")
                old_priority = original_task.get("priority", "N/A")
                new_priority = new_task.get("priority", "N/A")

                if old_title != new_title:
                    summary_parts.append(f'The task "{old_title}" has already changed to "{new_title} and updated manually, dont need to update the original task".')
                elif old_priority != new_priority:
                    summary_parts.append(f'The task "{new_title}" priority was changed from {old_priority} to {new_priority}.')
                else:
                    summary_parts.append(f'The task "{new_title}" was modified by the user.')

    # Track rejected tasks
    if rejected_keys:
        rejected_titles = [
            f'"{p["task"].get("title", "Untitled")}"'
            for p in proposed
            if p["key"] in rejected_keys
        ]
        if rejected_titles:
            summary_parts.append(f'The following task(s) were rejected: {", ".join(rejected_titles)}.')

    # Track new/accepted tasks (not edited, not rejected)
    new_tasks = [
        f'"{p["task"].get("title", "Untitled")}"'
        for p in proposed
        if p["key"] not in rejected_keys and p["key"] not in edited_tasks
    ]
    if new_tasks:
        summary_parts.append(f'New task(s) added: {", ".join(new_tasks)}.')

    # Track deleted tasks
    if deleted_count > 0:
        summary_parts.append(f'{deleted_count} task(s) were deleted.')

    return " ".join(summary_parts) if summary_parts else (
        f"Task memory updated ({upserts_count} upsert(s), {deleted_count} delete(s)) after human approval."
    )


def build_hitl_system_directive() -> str:
    """Return a system-level directive appended to the HITL summary.

    This tells the LLM that task updates have already been applied, so it
    should NOT call update_tasks again — just acknowledge and respond.
    """
    return (
        "\n\n[SYSTEM: The task changes above have already been saved to the database. "
        "Do NOT call update_tasks again. Simply acknowledge the result and respond "
        "to the user conversationally.]"
    )


# =============================================================================
# GENERIC DEBUG PRINTERS
# =============================================================================
def print_dict(data: dict, title: str = "Data") -> None:
    """Print a dictionary with formatting.

    Args:
        data: Dictionary to print
        title: Section title
    """
    print_section(title)
    for key, value in data.items():
        print(f"[DEBUG] {key}={value}")
    print_end_section()


def print_list(items: list, title: str = "Items", item_formatter=None) -> None:
    """Print a list with formatting.

    Args:
        items: List to print
        title: Section title
        item_formatter: Optional function to format each item
    """
    print_section(title)
    if not items:
        print("[DEBUG] No items")
    else:
        print(f"[DEBUG] Total: {len(items)}")
        for idx, item in enumerate(items, 1):
            if item_formatter:
                print(f"[DEBUG] [{idx}] {item_formatter(item)}")
            else:
                print(f"[DEBUG] [{idx}] {item}")
    print_end_section()
