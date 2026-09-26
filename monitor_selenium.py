import sys
import signal
from datetime import datetime
from vstudy_scraper import AuthenticationRequiredError, VStudyScraper
from results_db import ResultsDatabase
from telegram_notifier import TelegramNotifier


class VStudyMonitor:
    def __init__(self):
        self.scraper = VStudyScraper()
        self.database = ResultsDatabase()
        self.notifier = TelegramNotifier()
        signal.signal(signal.SIGINT, self._on_exit)

    def _on_exit(self, sig, frame):
        print("\n[!] Stopping monitor...")
        self.scraper.close()
        sys.exit(0)

    def _ts(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def run(self):
        print("=" * 60)
        print("   VSTUDY COURSE MONITOR")
        print("=" * 60)

        if not self.notifier.test_connection():
            print("[!] Telegram not connected — check your token/chat ID")

        print(f"[{self._ts()}] [*] Checking course results...")
        try:
            results = self.scraper.scrape_results()
        except AuthenticationRequiredError as exc:
            print(f"[AUTH REQUIRED] {exc}")
            if self.database.get_state("auth_warning_sent", "0") != "1":
                sent = self.notifier.notify_error(
                    "VStudy needs re-authentication. The monitor will retry automatically, "
                    "but a valid authenticated Chrome profile must be restored."
                )
                if sent:
                    self.database.set_state("auth_warning_sent", "1")
            return
        except Exception as exc:
            print(f"[✗] Scraping failed: {exc}")
            self.notifier.notify_error(
                "VStudy scraping failed. Check browser profile, network, or page availability."
            )
            return

        self.database.set_state("auth_warning_sent", "0")

        if results is None:
            print("[✗] Scraping failed!")
            self.notifier.notify_error("VStudy scraping failed. Check browser profile, network, or page availability.")
            return

        if len(results) == 0:
            print("[✓] No course results found")
            print("[✓] No new results")
            return

        # First successful production run establishes the current VStudy
        # state as the baseline without sending notifications for old results.
        if self.database.get_state("baseline_initialized", "0") != "1":
            print(f"[*] Establishing baseline with {len(results)} course result(s)...")
            for result in results:
                self.database.add_result(result)
                self.database.log_notification(
                    course_code=result.get("course_code", ""),
                    course_name=result.get("course_name", ""),
                    course_type=result.get("course_type", ""),
                    grade=result.get("grade", ""),
                    course_gpa=result.get("course_gpa", ""),
                    status=result.get("status", ""),
                    month_year=self._ts()[:7],
                    attendance=result.get("attendance", ""),
                    assessments=result.get("assessments", ""),
                    other_requirements=result.get("other_requirements", ""),
                )
            self.database.set_state("baseline_initialized", "1")
            print("[✓] Baseline saved; no notifications sent for existing results")
            return

        print(f"[*] Checking database for new results...")
        new = self.database.find_new_results(results)

        if not new:
            print("[✓] No new results")
            return

        print(f"[!] {len(new)} NEW result(s) found!")
        for result in new:
            self.database.add_result(result)

            sent = self.notifier.send_notification(
                result.get("course_code", ""),
                result.get("course_name", ""),
                result.get("grade", ""),
                result.get("status", ""),
                "",
                result.get("course_type", ""),
                result.get("course_gpa", ""),
            )

            if not sent:
                print(f"[!] Telegram notification failed for {result.get('course_code', '')}; skipping DB log for this course")
                continue

            self.database.log_notification(
                course_code=result.get("course_code", ""),
                course_name=result.get("course_name", ""),
                course_type=result.get("course_type", ""),
                grade=result.get("grade", ""),
                course_gpa=result.get("course_gpa", ""),
                status=result.get("status", ""),
                month_year=self._ts()[:7],
                attendance=result.get("attendance", ""),
                assessments=result.get("assessments", ""),
                other_requirements=result.get("other_requirements", ""),
            )

        print("[*] Done!")


if __name__ == "__main__":
    monitor = VStudyMonitor()
    monitor.run()