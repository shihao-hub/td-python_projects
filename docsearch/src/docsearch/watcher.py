import time
import threading
from pathlib import Path
from typing import Dict, Set
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer
from docsearch.service import SearchService


class DocumentDebounceHandler(FileSystemEventHandler):
    def __init__(self, service: SearchService, debounce_seconds: float = 0.8):
        super().__init__()
        self.service = service
        self.debounce_seconds = debounce_seconds
        self.pending_upserts: Dict[str, float] = {}
        self.pending_deletions: Set[str] = set()
        self.lock = threading.Lock()
        self.running = True
        self.worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self.worker_thread.start()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule_upsert(event.src_path)

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule_upsert(event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule_delete(event.src_path)

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule_delete(event.src_path)
            self._schedule_upsert(event.dest_path)

    def _schedule_upsert(self, path_str: str):
        path = Path(path_str)
        if not self.service.is_file_allowed(path):
            return
        with self.lock:
            self.pending_deletions.discard(path_str)
            self.pending_upserts[path_str] = time.time() + self.debounce_seconds

    def _schedule_delete(self, path_str: str):
        path = Path(path_str)
        if path.suffix.lower() not in [
            ext.lower() for ext in self.service.config.indexing.file_extensions
        ]:
            return
        with self.lock:
            self.pending_upserts.pop(path_str, None)
            self.pending_deletions.add(path_str)

    def _process_loop(self):
        while self.running:
            time.sleep(0.3)
            now = time.time()
            to_upsert = []
            to_delete = []

            with self.lock:
                # 收集已过防抖时间的文件
                for path_str, fire_time in list(self.pending_upserts.items()):
                    if now >= fire_time:
                        to_upsert.append(path_str)
                        del self.pending_upserts[path_str]

                # 收集删除任务
                to_delete = list(self.pending_deletions)
                self.pending_deletions.clear()

            for p_str in to_delete:
                try:
                    p = Path(p_str)
                    self.service.delete_single_file(p)
                    print(f"[docsearch watcher] 自动同步：已删除文档索引: {p.name}")
                except Exception as e:
                    print(f"[docsearch watcher] 删除索引失败 ({p_str}): {e}")

            for p_str in to_upsert:
                try:
                    p = Path(p_str)
                    if p.exists():
                        count = self.service.index_single_file(p, force=True)
                        print(f"[docsearch watcher] 自动同步：已更新文档索引: {p.name} (chunks: {count})")
                except Exception as e:
                    print(f"[docsearch watcher] 更新索引失败 ({p_str}): {e}")

    def stop(self):
        self.running = False


class DirectoryWatcher:
    def __init__(self, service: SearchService):
        self.service = service
        self.observer = Observer()
        self.handler = DocumentDebounceHandler(service)

    def start(self):
        watch_dirs = self.service.config.indexing.watch_dirs
        active_count = 0
        for dir_str in watch_dirs:
            p = Path(dir_str)
            if p.exists() and p.is_dir():
                self.observer.schedule(self.handler, str(p), recursive=True)
                active_count += 1
                print(f"[docsearch watcher] 监听目录: {p.resolve()}")

        if active_count == 0:
            print("[docsearch watcher] 警告：没有有效的目标目录可供监听！")

        self.observer.start()

    def stop(self):
        self.handler.stop()
        self.observer.stop()
        self.observer.join()