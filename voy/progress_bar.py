import random
import sys
import threading
import time


class Task:
    """Represents a single progress bar or spinner."""

    def __init__(self, prefix: str, total: int | None = None):
        self.prefix = prefix
        self.total = total  # None denotes a spinner
        self.current = 0
        self.done = False
        self.success = True

    def update(self, advance: int = 1):
        """Advances the progress bar."""
        if self.total is not None and not self.done:
            self.current = min(self.total, self.current + advance)
            if self.current >= self.total:
                self.done = True

    def complete(self, success: bool = True):
        """Marks the task as fully complete."""
        self.done = True
        self.success = success
        if self.total is not None:
            self.current = self.total


class Progress:
    """Manager for rendering multiple progress bars and spinners."""

    # uv-style Braille spinners and block progress characters
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    BAR_FILL = "-"
    BAR_EMPTY = "-"

    def __init__(self, cleanup: bool = False):
        self.tasks: list[Task] = []
        self.cleanup = cleanup
        self._running = False
        self._thread: threading.Thread | None = None
        self._frame_idx = 0
        self._lines_rendered = 0
        self._lock = threading.Lock()

    def track(self, iterable, prefix="", context_func=None, min_interval=0.05):
        """
        Iterator interface that automatically updates a progress bar.
        Includes a rate-limiter to prevent overhead on extremely fast loops.
        """
        try:
            total = len(iterable)
        except TypeError:
            total = None  # Fallback for generators without __len__

        pbar = self.add_task(prefix, total=total)
        pspn = self.add_spinner("") if context_func else None

        last_update = time.time()
        pending_advance = 0

        for item in iterable:
            # Yield control back to your business logic
            yield item
            pending_advance += 1

            # Rate limiter: Only update state if min_interval has passed
            now = time.time()
            if now - last_update >= min_interval:
                if pspn and context_func:
                    pspn.prefix = context_func(item)

                pbar.update(pending_advance)
                pending_advance = 0
                last_update = now

        # Flush any remaining progress when the loop finishes
        if pending_advance > 0:
            pbar.update(pending_advance)

        if pspn:
            pspn.complete()

    def add_task(self, prefix: str, total: int = 100) -> Task:
        with self._lock:
            task = Task(prefix, total=total)
            self.tasks.append(task)
            return task

    def add_spinner(self, prefix: str) -> Task:
        with self._lock:
            task = Task(prefix, total=None)
            self.tasks.append(task)
            return task

    def start(self):
        sys.stdout.write("\033[?25l")  # Hide cursor
        self._running = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

        self._render()  # Final render pass
        sys.stdout.write("\033[?25h")  # Show cursor

        if self.cleanup:
            self._clear_lines(len(self.tasks))

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _clear_lines(self, n: int):
        if n > 0:
            sys.stdout.write(f"\033[{n}A")  # Move cursor up
            for _ in range(n):
                sys.stdout.write("\033[2K\n")  # Clear line and move down
            sys.stdout.write(f"\033[{n}A")  # Move cursor back up
            sys.stdout.flush()

    def _render_loop(self):
        while self._running:
            self._render()
            time.sleep(0.08)  # 12.5 FPS for smooth spinning
            self._frame_idx += 1

    def _render(self):
        with self._lock:
            if self._lines_rendered > 0:
                sys.stdout.write(f"\033[{self._lines_rendered}A")

            output = []
            for task in self.tasks:
                # Determine icon state
                if task.done:
                    icon = "\033[32m✔\033[0m" if task.success else "\033[31m✖\033[0m"
                else:
                    spinner = self.FRAMES[self._frame_idx % len(self.FRAMES)]
                    icon = f"\033[36m{spinner}\033[0m"

                # Render Spinner vs Bar
                if task.total is None:
                    output.append(f"\033[2K\r{icon} {task.prefix}")
                else:
                    width = 20
                    percent = task.current / task.total if task.total > 0 else 1
                    filled = int(width * percent)

                    # Cyan for filled, dim gray for empty track
                    bar = f"\033[36m{self.BAR_FILL * filled}\033[90m{self.BAR_EMPTY * (width - filled)}\033[0m"
                    output.append(f"\033[2K\r{icon} {task.prefix} {bar}")

            sys.stdout.write("\n".join(output) + "\n")
            sys.stdout.flush()
            self._lines_rendered = len(self.tasks)


def main():
    print("Starting build process...\n")

    # Set cleanup=True if you want the bars to vanish entirely upon completion
    with Progress(cleanup=True) as p:
        # Spinners
        resolving = p.add_spinner("Resolving dependencies...")
        time.sleep(1.5)
        resolving.complete()

        # Concurrent Progress Bars
        task1 = p.add_task("Downloading core      ", total=50)
        task2 = p.add_task("Downloading plugins   ", total=80)
        task3 = p.add_task("Building extensions   ", total=40)

        while not (task1.done and task2.done and task3.done):
            time.sleep(0.1)
            task1.update(random.randint(0, 3))
            task2.update(random.randint(0, 2))
            task3.update(random.randint(0, 1))

    print("\n\033[32mBuild complete.\033[0m")


def mycase():
    lst = list("aăâbcdefghiîjklmnopqrsștțuvwxyz")
    bsz = 5
    batches = [lst[i : i + bsz] for i in range(0, len(lst), bsz)]

    with Progress(cleanup=True) as p:
        pbar = p.add_task("", total=len(lst))
        pspn = p.add_spinner("")

        for batch in batches:
            pspn.prefix = ",".join([f"{x}" for x in batch])
            time.sleep(1)
            pbar.update(len(batch))
        pspn.complete()


def mycase2():
    lst = list("aăâbcdefghiîjklmnopqrsștțuvwxyz")
    bsz = 5
    batches = [lst[i : i + bsz] for i in range(0, len(lst), bsz)]

    with Progress(cleanup=True) as p:
        pbar = p.track(batches, prefix="", context_func=lambda xs: ",".join(xs))
        for _ in pbar:
            time.sleep(1)

    print("\n\033[32m✔ All batches processed successfully.\033[0m")


if __name__ == "__main__":
    mycase2()
    mycase()
    main()
