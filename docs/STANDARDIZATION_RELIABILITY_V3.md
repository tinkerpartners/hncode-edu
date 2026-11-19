# Chuẩn hoá bài tập — sửa độ tin cậy và bộ hồi quy 50 bài

Lượt nguồn: 19/11/2026. Hoàn tất hồi quy: 20/11/2026. Nhánh: `codex/mrtee-hncode-standardization-v3`, dựa trên `main` sau khi PR #165 được merge (`f1e2fa2`).

## 1. Vấn đề đã sửa

### Kết quả về muộn không cập nhật quy trình

Trước đây bài nộp có thể đã AC/WA nhưng batch, tag và Points vẫn báo timeout. `record_candidate()` hiện gọi bộ đối soát dùng chung cho mọi đường kiểm chứng, gồm thao tác Verify, policy và máy chấm trong pipeline.

- Chỉ đối soát lời giải mới nhất trong batch và bản kiểm chứng mới nhất của lời giải đó.
- Kiểm tra hash đề, mã nguồn và cấu hình/bộ test hiện tại.
- AC đủ điểm có thể giải phóng phần tag/Points đang chờ máy chấm; tag chưa ánh xạ được, rating thiếu căn cứ và nội dung đề cần làm rõ vẫn giữ lại.
- Giữ nguyên quyết định duyệt, từ chối, yêu cầu sửa, bài đã áp dụng và batch đã dừng/hủy.
- Khoá dữ liệu theo thứ tự problem → batch → proposal; cập nhật trong transaction.
- Đối soát có thể chạy lại mà không tạo thêm đề xuất, bài nộp hoặc quyết định áp dụng.

### Timeout bị coi như điểm kết thúc pipeline

Sau timeout, Celery theo dõi **bài nộp đã có**, mỗi 30 giây. Mặc định tối đa 120 lần (khoảng một giờ), cấu hình bằng `STANDARDIZATION_GUIDED_POLL_MAX_ATTEMPTS`. Không chấm lại và không gọi AI trong lúc chờ.

- Kết quả AC: cập nhật batch và các phụ thuộc hợp lệ.
- Kết quả WA/CE/TLE... về muộn: có thể tiếp tục đúng bước sửa còn lại của lượt chạy ban đầu.
- Chỉ tiếp tục khi kết quả thuộc **chính bài nộp từng timeout**, cùng mã nguồn/cấu hình; kiểm tra lại khi worker thực sự bắt đầu.
- Chuỗi vẫn giới hạn Luna → một lượt sửa Luna → Sol nếu người tạo batch cho phép. Không chạy lại bước primary đã hoàn thành.
- Giữ nguyên budget của batch và các cap hiện có. Không mở lại batch bị pause/cancel hoặc chen vào một lượt chạy khác đang hoạt động.
- Lỗi thông thường được Verify thủ công không tự mở một lượt AI trả phí mới.
- Bài nộp đang chờ đã bị xóa được báo riêng, không tạo một bài nộp thay thế âm thầm.

Tác vụ tiếp tục chỉ được gửi sau commit. Worker nhận trùng tác vụ vẫn phải qua bước claim/kiểm tra trạng thái và bằng chứng.

### AI làm thay đổi dữ liệu ví dụ

Ví dụ đọc được từ nguồn được khôi phục trước khi render đề:

- AI chỉ thay khoảng trắng: phục hồi ví dụ gốc và không tạo cảnh báo sai chỉ vì thay đổi đã bị loại bỏ này.
- AI đổi số liệu hoặc làm mất ví dụ: phục hồi ví dụ gốc, đồng thời ghi `model_changed_source_examples` và giữ `needs_clarification`.
- Không nới checker, không dùng so sánh token để chứng nhận một ví dụ đã bị thay đổi là đúng.
- Không tự đoán ví dụ khi nguồn không trích được thành các cặp input/output.
- So sánh cấu trúc, nội dung, ảnh và tên file vẫn được giữ. Bản viết lại nội dung chưa có bằng chứng bảo toàn ý nghĩa không tự động được áp dụng.

### Prompt thuật toán

Phiên bản mới: `std_prompts_v4`. Bổ sung yêu cầu thử phá bất biến trên ca nhỏ độc lập; giữ đầy đủ trạng thái DP mà bước chuyển còn phụ thuộc; kiểm tra khép vòng/chu kỳ; bảo đảm subtask, diễn giải và code thống nhất. Không cho model tự nhận đã chạy vét cạn nếu không có bằng chứng thực thi.

Không có kết luận rằng prompt mới làm mọi bài đúng: đợt kiểm thử này tái sử dụng đầu ra AI cũ, không gọi lại model. Muốn đo tác động của prompt v4 cần một đợt benchmark AI riêng có ngân sách.

## 2. Đối soát dữ liệu cũ sau triển khai

Không có migration DB mới. Khởi động lại web và Celery worker cùng phiên bản code. Phải có worker xử lý các tác vụ mới trước khi bật pipeline.

Ví dụ đối soát bốn batch có kết quả về muộn trong đợt đã kiểm tra:

```sh
# Mặc định dry-run: chỉ báo thay đổi dự kiến, transaction được rollback.
python manage.py standardize_reconcile --batch-ids 77,78,82,87

# Chỉ đồng bộ trạng thái từ kết quả đã có; không gọi AI, không chấm lại,
# không approve/apply đề xuất và không mở lại batch.
python manage.py standardize_reconcile --batch-ids 77,78,82,87 --apply
```

ID trên là ID production của đợt audit, không phải ID local. Kiểm tra dry-run trên DB đích trước. Không có cập nhật production nào được thực hiện trong lần sửa này.

Policy có danh sách `prompt_versions_json` cần bổ sung `std_prompts_v4` khi người quản trị quyết định cho phép phiên bản mới. Không tự sửa policy hoặc bật auto-apply khi deploy.

## 3. Dữ liệu kiểm thử local

Trang xem: <http://localhost:8001/qa-standardization/?suite=recheck50>.

- Đúng 50 bài nguồn của lượt đã audit, kèm 241 đề xuất và 49 bản kiểm chứng lịch sử.
- 53 tệp ZIP/checker, tổng 933.800.935 byte, lưu trên ổ D. Dùng hard link cho dữ liệu QA khi có thể, tránh nhân đôi ZIP.
- DB riêng `hncode_openrouter_pilot`; mã bài `qa50_*`. Không ghi đè các bài trong DB local chính.
- Giữ cả đề gốc, Solution cũ, bản AI, tag/rating và các trường hợp thiếu đề/mâu thuẫn.
- Các khóa ngoại production được ánh xạ sang local; đề xuất cấu hình được lập lại theo ID local.
- Kết quả production mang nhãn `Production history`, mức `unverified`; không được dùng thay bằng chứng chấm local.
- Bản sao QA tắt key OpenRouter và batch import có budget bằng 0. Không đưa API key, tài khoản production hoặc hidden test vào Git.

Kết quả trên **bản AI gốc**:

| Phạm vi | Kết quả |
| --- | --- |
| 35 lời giải đủ điều kiện chấm lại | 34 AC đủ điểm, 1 WA (`flags`) |
| 15 bài còn lại | Giữ để bổ sung/làm rõ nguồn hoặc hợp đồng chấm |
| Replay bộ xử lý đề, không gọi AI | 24 bản JSON; phục hồi ví dụ ở 2 bản |
| `envchain` | Hết hold sai vì khoảng trắng; vẫn giữ gate bảo toàn ý nghĩa khi auto-apply |
| HTTP qua cổng 8001 | 50 dòng, 100 liên kết đề/đối chiếu trả 200; trang chủ chính trả 200 |

34/35 = 97,14% chỉ là tỷ lệ code đạt **bộ test hiện tại** trong nhóm đủ điều kiện chấm. Không phải tỷ lệ đúng tổng thể của đề, diễn giải, tag và độ khó. 15 bài chưa xác minh không tính là AC và cũng không tự quy thành lời giải sai.

## 4. Ca lỗi `flags`

Giữ nguyên bản AI sai tại `qa50_flags`. Tạo bản sửa riêng `qa50_flags_fixed` để so sánh.

- Sửa DP cây: giữ cả trạng thái có cờ của đỉnh và cha; bước ghép con phải dùng đúng trạng thái có cờ của con.
- DP chu trình giữ cặp trạng thái liên tiếp và kiểm tra đủ hai ràng buộc khép vòng.
- Subtask chu trình: có nghiệm khi độ dài chia hết cho 4, không phải mọi chu trình chẵn.
- Bản sửa dùng thời gian/bộ nhớ O(N), không đệ quy sâu.
- Qua 4.902 ca độc lập: mọi đồ thị đơn liên thông N đỉnh/N cạnh đến N=6, các ca ngẫu nhiên đến N=14, ca phản ví dụ và chu trình lớn tới 100.000 đỉnh. Ca nhỏ được đối chiếu với vét cạn tập đỉnh.
- AC 100/100 điểm test trên bộ test gốc đã sao chép về local.

Ca phản ví dụ công khai, tự tạo:

```text
4
1 2
2 3
3 1
1 4
```

Đáp án 2; code AI cũ in 1. Bản sửa và diễn giải chỉ được áp dụng vào bài QA riêng, không thay đổi bài trên production.

## 5. Kiểm thử và giới hạn

Bộ Django regression bao gồm cập nhật sau AC/WA về muộn, code/test/đề thay đổi, batch hủy, quyết định duyệt được giữ nguyên, claim tiếp tục đúng một lần, không lặp primary, budget được giữ, polling có giới hạn, pending submission mất, dry-run/idempotency và bảo toàn ví dụ. Có cả hồi quy apply/policy/hash/config và giao diện trang chủ sẵn có.

Lệnh trong môi trường đã cài dependency:

```sh
python manage.py test judge.tests judge.tests.test_home_layout --pattern='test_standardization*.py' --keepdb --noinput
python manage.py makemigrations --check --dry-run
```

Số lượng ca và log lần chạy cuối nằm trong báo cáo QA trên ổ D. Black/Ruff và `git diff --check` được kiểm tra riêng.

Các ZIP/checker, script thu thập, snapshot HTML/JSON, mật khẩu QA và báo cáo dữ liệu thực tế được giữ ngoài repository. Không commit hidden test hoặc thông tin đăng nhập.

Các giới hạn còn lại: AC không chứng minh tính đầy đủ của bộ test; không tự chứng nhận diễn giải hay rating chính xác; ảnh/PDF không đọc được và mâu thuẫn toán học vẫn phải có nguồn bổ sung. Đợt này không phát sinh chi phí API, không benchmark lại chất lượng model và không sửa policy production.
