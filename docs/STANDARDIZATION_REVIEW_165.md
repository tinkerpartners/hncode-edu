# Xử lý review PR #165

Review của `anhcx0209` ngày 19/11/2026 hợp lý: [review gốc](https://github.com/tinkerpartners/hncode-edu/pull/165#pullrequestreview-5974548232).

## Quyết định và thay đổi

| Góp ý | Xử lý |
|---|---|
| Rejudge WA/TLE bị lặp vô hạn mỗi lần policy sweep | Không tự chấm lại một kết quả FAILED của đúng hash code và cấu hình/test hiện tại. Áp dụng cả Solution và tags/Points liên kết. Đổi code/cấu hình mới được xét lại; Verify thủ công vẫn được phép. |
| Giới hạn UNKNOWN chỉ có hiệu lực trong một số trường hợp | Lưu hash cả khi tiền kiểm I/O hoặc hạ tầng lỗi; giới hạn ba UNKNOWN theo từng code/cấu hình, thời gian chờ 300 giây, chỉ tự thử lỗi máy chấm. |
| Mặc định tự áp dụng có thể tác động hai site | Giữ mặc định False trong repo. Bật riêng site khi triển khai policy đầu tiên. |
| Finished batch có thể chứa nhiều đề xuất v1 | Policy guided bắt buộc danh sách phiên bản rõ ràng; phiên bản không khớp bị giữ. Proposal cấu hình theo quy tắc mới cũng có version để lọc đúng. |
| Code tác giả ghép macro tên file bị bỏ qua | Cho code tác giả đi qua máy chấm thật, không dùng việc thiếu literal để kết luận sai. Giữ nguyên mã nguồn và kiểm chứng đủ điểm; không nới điều kiện chấp nhận. Chốt literal cho code sinh mới vẫn được giữ. |
| Link log ổ D không dùng được trên máy dev | Thay bằng tài liệu trong repo, lệnh tái lập và mô tả phạm vi bằng chứng. Không đưa test/snapshot riêng tư lên Git. |

Không chuyển proposal sang REWORK chỉ vì máy chấm lỗi. Sau FAILED đúng phiên bản, proposal vẫn chờ với lý do thất bại hiển thị; policy không tốn thêm lượt chấm vô hạn. Bản code đã sửa hoặc môi trường chấm thay đổi vẫn phải qua mọi điều kiện policy trước khi được áp dụng.

## Tái lập

Kết quả ngày 19/11/2026: **234/234 test đạt** (toàn bộ standardization và 11 test home layout) trên DB kiểm thử riêng. Black, Ruff và `git diff --check` đạt. Không có thay đổi model, prompt hoặc schema trong lần sửa review này; không cần migration mới và không gọi AI trả phí.

Dùng môi trường Python, dependency và cơ sở dữ liệu kiểm thử theo cấu hình của dự án. Các bài kiểm thử dùng fixture/provider giả lập, không cần API key và không gọi AI trả phí.

```sh
python manage.py test judge.tests judge.tests.test_home_layout --pattern='test_standardization*.py' --noinput
python -m black --check judge/standardization judge/tests/test_standardization_rejudge.py
python -m ruff check judge/standardization judge/tests/test_standardization_rejudge.py
git diff --check
```

Các test hồi quy tập trung ở:

- `judge/tests/test_standardization_rejudge.py`: một rejudge thất bại, các sweep kế tiếp và proposal liên kết không chấm tiếp; đổi hash code/config cho phép kiểm chứng mới; UNKNOWN cooldown và số lần thử theo phiên bản.
- `judge/tests/test_standardization_policy.py`: mặc định phải opt-in riêng và khai báo danh sách prompt version, nháp khác version bị giữ.
- `judge/tests/test_standardization_guided.py`: code tác giả dùng macro filename vẫn được gọi máy chấm, giữ nguyên code tham chiếu; luồng cấu hình → rejudge → áp dụng vẫn hoạt động dưới policy có version.
- `judge/tests/test_standardization_verification.py`: Verify code macro tham chiếu đi qua judge thật; tiền kiểm FAILED có hash chính xác để policy nhận diện kết quả cuối.

Máy chấm trong các unit/integration test được giả lập; kiểm chứng bằng chương trình thật trên một phần dữ liệu demo đã được mô tả riêng trong [bàn giao](STANDARDIZATION_V2_HANDOFF.md). Không coi một phần test demo là full verdict production.

Chưa deploy hoặc thay đổi cấu hình/policy production khi xử lý review này.
