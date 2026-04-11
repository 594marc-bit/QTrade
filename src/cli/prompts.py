"""Generic CLI prompt utilities for the interactive wizard."""

from rich.console import Console

console = Console()


def select(prompt: str, options: list[str], default: int = 1) -> int:
    """Display numbered options and return the selected index (1-based).

    Args:
        prompt: Question text.
        options: List of option strings.
        default: Default option number (1-based). User can press Enter to accept.

    Returns:
        Selected option number (1-based).
    """
    console.print(f"\n[bold cyan]{prompt}[/]")
    for i, opt in enumerate(options, 1):
        marker = " [dim](默认)[/dim]" if i == default else ""
        console.print(f"  {i}. {opt}{marker}")

    while True:
        raw = input(f"请选择 [1-{len(options)}] (默认 {default}): ").strip()
        if not raw:
            return default
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
            console.print(f"[red]请输入 1-{len(options)} 之间的数字[/red]")
        except ValueError:
            console.print("[red]请输入有效数字[/red]")


def input_value(prompt: str, default: str = "", value_type: type = str) -> str:
    """Prompt for a value with a default.

    Args:
        prompt: Question text.
        default: Default value string.
        value_type: Type to validate (str, int, float).

    Returns:
        User input value as string, or default if empty.
    """
    default_hint = f" (默认: {default})" if default else ""
    raw = input(f"{prompt}{default_hint}: ").strip()
    if not raw:
        return str(default)

    if value_type in (int, float):
        try:
            value_type(raw)
        except ValueError:
            console.print(f"[red]请输入有效的{value_type.__name__}[/red]")
            return input_value(prompt, default, value_type)

    return raw


def confirm(prompt: str, default: bool = True) -> bool:
    """Yes/No confirmation prompt.

    Args:
        prompt: Question text.
        default: Default value. True shows (Y/n), False shows (y/N).

    Returns:
        Boolean choice.
    """
    hint = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "是")


def multi_select(
    prompt: str,
    options: list[dict],
    id_key: str = "id",
    label_key: str = "label",
    defaults: set[str] | None = None,
) -> set[str]:
    """Display toggleable multi-select list.

    Args:
        prompt: Header text.
        options: List of dicts with at least id_key and label_key.
        id_key: Key for the option identifier.
        label_key: Key for the display label.
        defaults: Set of pre-selected IDs.

    Returns:
        Set of selected IDs.
    """
    if defaults is None:
        defaults = set()

    selected = set(defaults)

    while True:
        console.print(f"\n[bold cyan]{prompt}[/]")
        console.print("  [dim]输入编号切换选中状态，多个编号用逗号分隔，直接回车确认[/dim]")
        for i, opt in enumerate(options, 1):
            oid = opt[id_key]
            marker = "[green]✓[/green]" if oid in selected else "[dim]○[/dim]"
            console.print(f"  {marker} {i}. {opt[label_key]}")

        if not selected:
            console.print("[yellow]至少需要选择一项[/yellow]")

        raw = input("请输入编号 (直接回车确认): ").strip()
        if not raw:
            if not selected:
                console.print("[red]至少需要选择一项，请重新选择[/red]")
                continue
            return selected

        # Parse comma-separated numbers
        for part in raw.split(","):
            part = part.strip()
            try:
                idx = int(part)
                if 1 <= idx <= len(options):
                    oid = options[idx - 1][id_key]
                    if oid in selected:
                        selected.discard(oid)
                    else:
                        selected.add(oid)
                else:
                    console.print(f"[red]编号 {idx} 超出范围[/red]")
            except ValueError:
                console.print(f"[red]无效输入: {part}[/red]")
