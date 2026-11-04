"""Judge-facing half of IDE runs (A22).

A run travels the ordinary submission protocol with a negative id (see
CodeRun.judge_id), so the judge cannot tell it from a submission. The bridge
can: every packet whose submission-id is negative is routed here instead of to
the Submission handlers, which would otherwise go looking for a Submission row
and, worse, touch points and rankings.
"""

import logging
import threading

from django.utils import timezone

from judge.bridge.utils import VanishedSubmission
from judge.models import CodeRun

logger = logging.getLogger("judge.bridge")


def _result_from_status(status):
    if status & 4:
        return "TLE"
    if status & 8:
        return "MLE"
    if status & 64:
        return "OLE"
    if status & 2:
        return "RTE"
    if status & 16:
        return "IR"
    if status & 1:
        return "WA"
    return "AC"


class RunHandlerMixin:
    def _run_handlers(self):
        return {
            "submission-acknowledged": self.on_run_acknowledged,
            "grading-begin": self.on_run_grading_begin,
            "grading-end": self.on_run_grading_end,
            "compile-error": self.on_run_compile_error,
            "compile-message": self.on_run_compile_message,
            "batch-begin": self._ignore_run_packet,
            "batch-end": self._ignore_run_packet,
            "test-case-status": self.on_run_test_case,
            "internal-error": self.on_run_internal_error,
            "submission-terminated": self.on_run_terminated,
        }

    @staticmethod
    def is_run_packet(packet):
        return CodeRun.is_run_judge_id(packet.get("submission-id"))

    @staticmethod
    def _run_id(packet):
        return CodeRun.from_judge_id(packet["submission-id"])

    def _ignore_run_packet(self, packet):
        pass

    def _update_run(self, packet, **fields):
        return CodeRun.objects.filter(id=self._run_id(packet)).update(**fields)

    def _finish_run(self, packet, **fields):
        """Free the judge and record the final state. The run's directory is
        left for the next run's sweep (see judge/utils/code_run.py)."""
        run_id = self._run_id(packet)
        self._free_self(packet)

        fields.setdefault("finished", timezone.now())
        if not CodeRun.objects.filter(
            id=run_id, status__in=CodeRun.IN_PROGRESS_STATUS
        ).update(**fields):
            logger.warning("Unknown or already finished run: %s", run_id)

    def submit_run(self, judge_id, problem, language, source):
        run_id = CodeRun.from_judge_id(judge_id)
        try:
            run = CodeRun.objects.only("time_limit", "memory_limit", "user_id").get(
                id=run_id
            )
        except CodeRun.DoesNotExist:
            logger.error("Run vanished: %s", run_id)
            raise VanishedSubmission()

        self._working = judge_id
        self._working_data = {
            "problem": problem,
            "language": language,
            "source": source,
        }
        self._no_response_job = threading.Timer(20, self._kill_if_no_response)
        self._no_response_job.start()
        self.send(
            {
                "name": "submission-request",
                "submission-id": judge_id,
                "problem-id": problem,
                "language": language,
                "source": source,
                "time-limit": run.time_limit,
                "memory-limit": run.memory_limit,
                "short-circuit": False,
                "meta": {
                    "pretests-only": False,
                    "in-contest": None,
                    "attempt-no": 1,
                    "user": run.user_id,
                    "file-only": False,
                    "file-size-limit": 0,
                },
            }
        )

    def on_run_acknowledged(self, packet):
        if packet.get("submission-id") != self._working:
            logger.error(
                "Wrong run acknowledgement: %s: %s, expected: %s",
                self.name,
                packet.get("submission-id"),
                self._working,
            )
            self.close()
            return
        if self._no_response_job:
            self._no_response_job.cancel()
            self._no_response_job = None
        self._update_run(packet, status="P", judged_on=self.name or "")

    def on_run_grading_begin(self, packet):
        self._update_run(packet, status="G")

    def on_run_compile_message(self, packet):
        self._update_run(packet, error=packet.get("log") or "")

    def on_run_compile_error(self, packet):
        self._finish_run(packet, status="CE", error=packet.get("log") or "")

    def on_run_test_case(self, packet):
        run_id = self._run_id(packet)
        try:
            run = CodeRun.objects.only("results").get(id=run_id)
        except CodeRun.DoesNotExist:
            logger.warning("Unknown run: %s", run_id)
            return
        results = list(run.results or [])
        for case in packet.get("cases") or []:
            position = case.get("position") or len(results) + 1
            entry = {
                "position": position,
                "result": _result_from_status(case["status"]),
                "time": case.get("time"),
                "memory": case.get("memory"),
                "output": case.get("output") or "",
                "feedback": (case.get("feedback") or "")[:255],
                "extended_feedback": case.get("extended-feedback") or "",
            }
            # A judge may resend a case (reconnect); keep one entry per case.
            results = [r for r in results if r.get("position") != position]
            results.append(entry)
        results.sort(key=lambda r: r.get("position") or 0)
        CodeRun.objects.filter(id=run_id).update(results=results)

    def on_run_grading_end(self, packet):
        self._finish_run(packet, status="D")

    def on_run_internal_error(self, packet):
        logger.error(
            "%s: internal error on run %s: %s",
            self.name,
            self._run_id(packet),
            packet.get("message"),
        )
        self._finish_run(
            packet, status="IE", error=(packet.get("message") or "")[-2000:]
        )

    def on_run_terminated(self, packet):
        self._finish_run(packet, status="AB")
