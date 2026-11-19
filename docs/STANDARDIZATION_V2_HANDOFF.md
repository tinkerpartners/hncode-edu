# Bàn giao nâng cấp chuẩn hoá bài tập v2

Ngày: 19/11/2026. Nhánh: `codex/mrtee-hncode-standardization-v2`, phát triển từ `main` tại `7e52f45` (PR #164 đã merge).

## Kết quả

Hoàn thiện quy trình dựa trên các lỗi của demo 50 bài. Luồng dùng lại policy, audit, snapshot và cơ chế hoàn tác hiện có; không bổ sung một hệ thống áp dụng riêng.

- Hợp đồng chấm lấy từ DB và metadata `init.yml`; đối chiếu với đề trước khi gọi AI. Kiểm tra tên file phân biệt hoa/thường, không gửi dữ liệu test ẩn vào prompt.
- Adapter C++ cho code sinh mới chỉ bổ sung nối stdin/stdout sang đúng file khi có thể xác định an toàn. Code tác giả không bị sửa. Mọi bản sau adapter vẫn phải chấm đủ điểm.
- Prompt/schema `std_prompts_v3` cho đề, lời giải, tags/rating. Lời giải + tags + rating được sinh chung một lượt. Tách cảnh báo trình bày khỏi mơ hồ ảnh hưởng đáp án, không tự chế ràng buộc/ví dụ/subtask hoặc nguồn Codeforces.
- Hướng dẫn bài dễ ngắn, dễ hiểu và giữ code mẫu; bài khó đi từ hướng dẫn các subtask có thật tới lời giải đầy đủ. Renderer tách từng subtask thành mục riêng, subtask dễ ngắn gọn, chỉ giữ một code cuối giải toàn bộ bài.
- Renderer giữ thông tin file; kiểm tra ví dụ nguồn–JSON–Markdown, nội dung sai trường, ảnh và ký tự điều khiển. Đề viết lại chỉ được tự áp dụng khi có chứng minh bảo toàn độc lập; điều kiện được tính lại khi xét policy.
- Points ước lượng có khoảng, độ tin cậy và lý do; chỉ được đưa sang chờ duyệt khi lời giải đạt test và dữ liệu rating hợp lệ. Áp dụng Points/tags phải kiểm chứng lại lời giải liên kết nếu bằng chứng đã cũ.
- Batch kết thúc vẫn có thể được policy xét; các cập nhật tự động cùng snapshot có chuỗi hash để không chặn nhầm nhau. Khoá policy/bài khi áp dụng; nguồn/test thay đổi ngoài chuỗi vẫn chặn.
- Cấu hình đích được xét trước, có thể chấm lại code đã lưu trước khi áp dụng lời giải/tags. Không trả phí gọi model lại cho bước này.
- Timeout mới lưu submission ID và hash để Verify tiếp tục đọc đúng lượt chấm cũ, tránh tạo submission trùng.
- Có lệnh `standardize_guided` chạy theo ID/mã với giới hạn, ngân sách và dry-run mặc định; form Batch admin dùng raw ID thay vì tải toàn bộ dropdown người dùng.

## Kiểm thử đã thực hiện

| Hạng mục | Kết quả | Phạm vi |
|---|---|---|
| Toàn bộ `test_standardization*.py` và `test_home_layout` | 234 test đạt | DB kiểm thử riêng, gồm hồi quy review #165, không gọi API thật |
| Kiểm thử policy sau lần siết điều kiện cuối | Chạy lại riêng module | Chốt bảo toàn áp dụng cả proposal AI cũ không có cờ v2 |
| Luồng tự áp dụng xuyên các tầng | Đạt | Một lượt AI giả lập, chấm dưới cấu hình cũ, áp dụng cấu hình, chấm lại, áp dụng Solution và tags; không gọi AI thêm |
| Adapter thật trên `lqdoj2022r6dyslexia` | 101/101 lượt chạy đúng | Test đầu đã tải từ server + 100 test nhỏ đối chiếu vét cạn |
| Adapter thật trên `pfsqpairs` | 29/29 lượt chạy đúng | Test đầu + 28 test tĩnh lưu ngày 18/11, có thể trùng test đầu |
| Adapter thật trên `pvhoi4i` | 13/13 lượt chạy đúng | Test đầu + 12 gói oracle nhỏ độc lập |
| Kiểm tra lại 45 bản đề của demo | 45 bản được xét | 18 bản sửa heading và 27 bản AI; phát hiện 13 bản có lỗi cấu trúc, cả bốn bản có vấn đề đã báo cáo đều không đủ điều kiện tự áp dụng |
| Black, Ruff, `git diff --check` | Đạt | Các file thay đổi |

Ba chương trình được biên dịch C++17 và chạy I/O file trong container không mạng, user không phải root, filesystem chỉ đọc ngoài `/tmp`, giới hạn RAM/PID. 143 lượt chạy đúng **không phải** xác nhận toàn bộ test production của cả ba bài.

Đối chiếu lại bản đề cũ bằng JSON `before_json` để giữ đúng xuống dòng; không dùng nội dung TextField đã render thành HTML trên admin làm bản xuất byte-for-byte. 18 bản heading đạt chứng minh bảo toàn; 27 bản viết lại bằng prompt cũ chưa đạt điều kiện bảo toàn tự động nghiêm ngặt. Không suy ra tỷ lệ tự động của prompt v2 từ kết quả này.

## Giới hạn và cấu hình khi triển khai

1. Chưa deploy production, chưa áp dụng hay sửa proposal của lượt demo trên HNCode. Không có migration mới.
2. Chưa chạy benchmark API với prompt v3: runtime local đang không có `OPENROUTER_API_KEY`. Các test AI dùng provider giả lập; cần một lô nhỏ có API thật để đo chất lượng ngôn ngữ, tỷ lệ được tự áp dụng và chi phí mới trước khi mở rộng.
3. Giữ Luna/Sol mặc định hiện có; chưa đổi model khi chưa có thử nghiệm đối chứng phù hợp. Model vẫn cấu hình được.
4. `STANDARDIZATION_GUIDED_AUTO_APPLY=False` mặc định giữ guided chờ duyệt. Bật `True` riêng site trong `local_settings.py` khi triển khai policy có phạm vi, danh sách phiên bản prompt bắt buộc, daily/total cap và hậu kiểm mẫu; không tự tạo hoặc bật policy.
5. Độ khó AI vẫn cần hiệu chuẩn mẫu theo prompt/bước/model trước khi tự áp dụng; nguồn thiếu, mâu thuẫn, bảng/phần code chưa chứng minh bảo toàn hoặc thuật toán chưa đạt test vẫn được giữ lại. Không dùng nhãn tự tin của AI để bỏ qua các chốt này.
6. Kiểm tra source hash và rejudge khi cấu hình/đề thay đổi; không coi AC của phiên bản trước là bằng chứng còn hiệu lực.
7. Trần API dựa trên tiền đã ghi nhận, không đặt chỗ chi phí cho request đang chạy; có thể vượt số dư còn lại của cap ở một request.

## Review đề nghị cho dev lead

- `contracts.py`, `verification.py`: nguồn hợp đồng, xung đột DB/init/đề, adapter, timeout và gắn bằng chứng vào đúng phiên bản code/test.
- `integrity.py`, `format_v1.py`: bảo toàn dữ kiện/ví dụ/ảnh/tên file và việc chặn bản viết lại ngữ nghĩa chưa được chứng minh.
- `steps.py`, `prompts.py`: schema mới, đề xuất rating cùng lời giải, tách cảnh báo, chống làm theo chỉ dẫn nhúng trong nguồn.
- `policy.py`, `apply.py`: chuỗi hash, khoá đồng thời, giới hạn áp dụng, điều kiện của Solution/tags/Points và hoàn tác.
- `standardize_guided.py`: dry-run, quyền actor, chọn theo lô, chống tạo trùng và xử lý worker dispatch lỗi.

Hướng dẫn lệnh, policy và vận hành chi tiết: [STANDARDIZATION_AUTOMATION_V2.md](STANDARDIZATION_AUTOMATION_V2.md).

## Tái lập kiểm thử và xử lý review

Kết quả và lệnh tái lập không phụ thuộc đường dẫn máy cá nhân: [STANDARDIZATION_REVIEW_165.md](STANDARDIZATION_REVIEW_165.md).

Các file test và snapshot riêng tư của demo được giữ ngoài Git; số liệu ở trên là kết quả quan sát, không phải bộ dữ liệu kèm repository. Dev có thể tái lập toàn bộ test tự động bằng fixture của repo và DB kiểm thử riêng. Tài liệu không chứa khoá API hoặc mật khẩu.
