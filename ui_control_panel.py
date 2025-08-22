# ui_control_panel.py
# A standalone module to create and manage an automation control panel.
# Renamed from automation_panel.py.

import tkinter as tk
import threading
import logging

class AutomationState:
    """
    A thread-safe class to manage and share the state of the automation process.
    Uses a lock to ensure safety when accessed from multiple threads.
    """
    def __init__(self):
        self._status = "running"  # Initial state
        self._lock = threading.Lock()

    @property
    def status(self):
        with self._lock:
            return self._status

    def pause(self):
        with self._lock:
            if self._status == "running":
                self._status = "paused"
                logging.info("Automation state changed to PAUSED.")
                return True
        return False

    def resume(self):
        with self._lock:
            if self._status == "paused":
                self._status = "running"
                logging.info("Automation state changed to RUNNING.")
                return True
        return False

    def stop(self):
        with self._lock:
            self._status = "stopped"
            logging.info("Automation state changed to STOPPED.")

    def is_stopped(self):
        with self._lock:
            return self._status == "stopped"

    def is_paused(self):
        with self._lock:
            return self._status == "paused"


class AutomationControlPanel:
    """
    Creates a small, always-on-top window with Pause, Resume, and Stop buttons.
    """
    def __init__(self, automation_state, notifier_instance=None):
        """
        Args:
            automation_state (AutomationState): The object to share state.
            notifier_instance: (Optional) A StatusNotifier instance to display messages.
        """
        if not isinstance(automation_state, AutomationState):
            raise TypeError("automation_state must be an instance of the AutomationState class.")
            
        self.state = automation_state
        self.notifier = notifier_instance
        self.root = None
        
        self.thread = threading.Thread(target=self._run_gui, daemon=True)
        self.thread.start()

    def _run_gui(self):
        self.root = tk.Tk()
        self.root.title("Ctrl")
        self.root.geometry("160x55+10+10") # Position in the top-left corner
        self.root.wm_attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._stop_automation) # Stop if the window is closed

        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=10, padx=10, fill='x', expand=True)

        self.pause_button = tk.Button(button_frame, text="⏸️ Pause", command=self._toggle_pause, width=9)
        self.pause_button.pack(side='left', padx=2)

        stop_button = tk.Button(button_frame, text="⏹️ Stop", command=self._stop_automation, width=9)
        stop_button.pack(side='left', padx=2)
        
        self.root.mainloop()

    def _toggle_pause(self):
        if self.state.status == 'running':
            if self.state.pause():
                self.pause_button.config(text="▶️ Resume")
                if self.notifier:
                    self.notifier.update_status("Paused by user.", style='warning', duration=0)
        elif self.state.status == 'paused':
            if self.state.resume():
                self.pause_button.config(text="⏸️ Pause")
                if self.notifier:
                    self.notifier.update_status("Resuming execution...", style='success', duration=3)

    def _stop_automation(self):
        self.state.stop()
        if self.notifier:
            self.notifier.update_status("Task has been stopped!", style='error', duration=0)
        try:
            self.pause_button.config(state='disabled')
            # Disable the stop button as well
            for widget in self.pause_button.master.winfo_children():
                if isinstance(widget, tk.Button) and 'Stop' in widget.cget('text'):
                    widget.config(state='disabled')
            # Close the window after a short delay
            self.root.after(2000, self.root.destroy)
        except tk.TclError:
            # This can happen if the window is already destroyed
            pass

    def close(self):
        """Closes the control panel window from an external call."""
        if self.root and self.root.winfo_exists():
            self.root.destroy()
