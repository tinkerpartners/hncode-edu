# Evidence RC3 — 14/11/2026

Source chức năng: `b5da9b3`. Môi trường local, dữ liệu tổng hợp.

- `full-suite-final.log`: 1.374 test PASS/148,329 giây. Các lỗi giả lập trong log là tình huống test, kết quả cuối là OK.
- `targeted-tests.log`: 68 test PASS/8,988 giây.
- `mobile-run.json` và `mobile-run-visible.png`: viewport 390×844, console top khoảng 101px sau Run; kết quả Accepted, output 3.
- `mobile-submit.json` và `mobile-submit-ac.png`: submission #86, Accepted 100/100 trong IDE; kết quả nằm trong viewport. Không chuyển trang kết quả.
- `rapid-navigation.json`, `sidebar-kept-collapsed.png`: chuyển nhanh bài 3→4 dưới latency 1.000ms, response cũ bị hủy, trang cuối qa_echo và cột vẫn thu gọn.
- `ranking-empty.png`: tìm kiếm không có kết quả, có thông báo và nút xóa lọc; thao tác xóa trả lại hai người.
- `ranking-offline.png`: mất mạng giả lập, giữ hai dòng dữ liệu cũ và hiện nút Thử lại.
- `ranking-retry-global-rank.png`, `ranking-filtered.xlsx`, `xlsx-checks.json`: khôi phục mạng, lọc qa_rank_002 còn một dòng, hạng 2, điểm 40 trong HTML và Excel.
- `backend-before-rc3.json`, `backend-results.json`: trước/sau, 28 tổ hợp route/vai trò, warmup và 5 mẫu nối tiếp mỗi tổ hợp. Benchmark không bao gồm network/browser, không phải tải production.
- `course-grades-profile.txt`, `page-assets.json`: profile backend và tài nguyên HTML; gzip ở script là ước lượng nén offline, không chứng minh response đã nén.
- `audit_backend.py`: script tái đo bằng Django shell; đổi ROOT và tạo fixture/tài khoản tương ứng trên QA. Dành cho QA có dữ liệu tổng hợp/tài khoản tương ứng; force_login tạo phiên test và poll thay đổi dùng since=outdated, không sửa điểm.

Đã khôi phục network/viewport browser và giữ dữ liệu local. Không kèm mật khẩu/token/DB dump.
