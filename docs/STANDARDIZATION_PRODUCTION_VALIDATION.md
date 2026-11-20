# Báo cáo nâng cấp luồng chuẩn hoá tự động

> Đây là báo cáo trước lượt chạy AI thật. Kết quả mới nhất (22 ca, lỗi tìm thấy và đã sửa, 325 test, chi phí thực tế) nằm trong [STANDARDIZATION_LIVE_LOCAL_REPORT.md](STANDARDIZATION_LIVE_LOCAL_REPORT.md). Số liệu 319 test và trạng thái 150 fixture bên dưới được giữ để đối chiếu lịch sử.

Ngày: 20/11/2026. Nhánh: `codex/mrtee-hncode-standardization-v3`.

## 1. Đã hoàn thiện

1. **Tự xét và áp dụng sau lượt chạy:** finalizer chọn policy đã bật, đúng bài/phạm vi/phiên bản, xét từng trường và ghi qua cơ chế audit hiện có. Kết quả chấm đến muộn cũng có tác vụ hoàn tất.
2. **Giảm phụ thuộc duyệt tay:** có profile phản biện riêng kết hợp code tác giả đã chấm đủ test. Profile giới hạn nhóm bài đơn giản, không dùng hai AI đồng ý hoặc confidence thay cho toàn bộ bằng chứng.
3. **Xử lý kết quả cũ tiết kiệm:** có thể thay một bản viết lại AI chưa chứng minh được bằng phép chuẩn hoá bảo toàn từ nguồn gốc, không gọi AI mới. Không vượt quyết định giữ/từ chối của người duyệt hoặc nguồn đã thay đổi.
4. **Danh sách theo từng vấn đề:** nguồn, test/hợp đồng, bố cục đề, hướng dẫn, tag, độ khó, cấu hình, hạ tầng/ngân sách và policy. Các proposal bị giữ, không vào sweep, vẫn có thể xuất hiện trong danh sách tương ứng.
5. **Reservation ngân sách:** giữ chỗ nguyên tử trước request; tính cả request đang chạy/chưa rõ tiền; chặn model chưa cấu hình ceiling; dừng nhận AI mới khi vượt ceiling. Có lệnh đối chiếu billing kèm người xác nhận và bằng chứng.
6. **Chứng cứ đúng phiên bản:** sửa đề, code, test, tag hoặc rating liên quan sẽ vô hiệu hoá chứng cứ không còn khớp. Kết quả về trong lúc dữ liệu đã đổi không được tự áp dụng cho dữ liệu mới.

Các nhóm đã đạt không chờ các nhóm chưa đạt. Ví dụ hướng dẫn có căn cứ nhưng Points còn chênh lệch thì chỉ Points được giữ riêng. Một lỗi có thể làm thay đổi nghĩa bài sẽ chặn cả bài, không cho cấu hình/tag đi vòng qua.

## 2. Kiểm thử phần mềm

**Kết quả lượt cuối: 319/319 test đạt, 80,308 giây.** Black, Ruff và `git diff --check` đạt; migration check không có thay đổi chưa tạo migration.

Kiểm thử gồm:

- Code tác giả + full judge + phản biện phù hợp cho phép tự áp dụng mà không chờ đủ số mẫu hiệu chuẩn cũ.
- Chỉ có đồng thuận AI, thiếu full judge hoặc code tác giả đổi: từ chối.
- Model viết và model phản biện trùng nhau hoặc reviewer ngoài danh sách: từ chối.
- Một model đánh giá bài khó, khoảng rating quá rộng hoặc ngoài phạm vi: không thông qua profile tự động.
- Rating/tag không thống nhất không tự làm mất các phần đã đủ căn cứ.
- Dữ liệu bị sửa trong lúc chờ phản biện: không cấp chứng cứ cho bản cũ.
- Request lỗi/chưa rõ chi phí: giữ reservation, không gửi lặp lại theo mỗi sweep.
- Hai kết nối DB đồng thời tranh phần ngân sách còn lại: chỉ request đủ ngân sách được nhận.
- Sai một mẫu hậu kiểm trong profile mới: dừng policy ngay.
- Danh sách phân trang theo vấn đề, tách lỗi hạ tầng khỏi nội dung cần duyệt và bảo vệ quyền thao tác.
- Chuẩn hoá bảo toàn từ dữ liệu gốc thay bản viết lại AI; tôn trọng quyết định giữ của người duyệt.

Phản hồi AI trong unit test là giả lập. Các test này kiểm tra logic phần mềm với đầu vào được kiểm soát, không đo tỷ lệ model giải đúng các bài toán thật.

## 3. Đối chiếu dữ liệu 150 bài trên local

Đã cập nhật phân luồng cho 150 fixture cũ, giữ nguyên hash nguồn và số proposal, decision, usage:

| Kết quả toàn bài | Số bài |
| --- | ---: |
| Thiếu căn cứ/dữ liệu hoặc cần luồng đặc biệt | 36 |
| Có nguồn nhưng còn trường chưa đủ điều kiện tự áp dụng ở cấu hình QA | 114 |

Trong tập này có **9 bài** có code tác giả khớp và kết quả full judge còn hiệu lực — đây mới là ứng viên cho bước phản biện, không phải 9 bài đã được phản biện thông qua. Các bài còn lại vẫn có thể tự xử lý phần đề/cấu hình đủ căn cứ; không được tự chứng nhận lời giải bằng profile code tác giả nếu chưa có nguồn tương ứng.

Đã chạy finalizer thật trên **10 fixture**, trong giao dịch hoàn tác:

- 10/10 áp dụng được phần nội dung bảo toàn từ nguồn gốc mà không gọi AI.
- Toàn bộ thay đổi được rollback; hash nguồn và số decision/usage giữ nguyên.
- Không kích hoạt profile phản biện thật hoặc policy production để có kết quả này.

Các trang phân nhóm nguồn, test, bố cục, hướng dẫn, tag, độ khó và hạ tầng đã được render thành công. Kiểm tra thêm qua gateway đang chạy cho 7 nhóm và trang chính: HTTP 200; trang internal `private, no-store`.

## 4. Trạng thái local và production

- Runtime QA đã cập nhật trên ổ D, vẫn sử dụng cổng 8001 và tiền tố `/qa-standardization/`.
- Migration QA `0286_standardization_reservations` và `0287_reservation_resolution` đã áp dụng; migration check không phát hiện thiếu migration.
- Các cờ tự áp dụng và phản biện thật trên QA vẫn tắt. Không phát sinh lượt AI trả phí mới trong đợt triển khai này.
- Chưa bật cờ/policy production, chưa sửa bài trên HNCode thật.
- Chưa kiểm tra ảnh chụp giao diện/Safari thật trong đợt này; có kiểm tra render, phân quyền và HTTP.

Các link kiểm tra:

- [Dashboard](http://localhost:8001/qa-standardization/internal/standardization/)
- [Đề/nguồn](http://localhost:8001/qa-standardization/internal/standardization/?route=all&status=&issue=source)
- [Hướng dẫn](http://localhost:8001/qa-standardization/internal/standardization/?route=all&status=&issue=solution)
- [Tag](http://localhost:8001/qa-standardization/internal/standardization/?route=all&status=&issue=tags)
- [Độ khó](http://localhost:8001/qa-standardization/internal/standardization/?route=all&status=&issue=points)
- [Hạ tầng/ngân sách](http://localhost:8001/qa-standardization/internal/standardization/?route=all&status=&issue=infrastructure)

## 5. Điều kiện trước khi mở rộng chạy thật

Phần mềm đã có đường chạy tự áp dụng có kiểm soát; chưa thể dùng test giả lập để tuyên bố reviewer AI thật đủ chính xác. Cần cấu hình model, ceiling, ngân sách và policy có phạm vi, rồi chạy một lô nhỏ có phản biện thật và hậu kiểm trước khi tăng số lượng.

Ceiling là ước tính phục vụ admission, không phải trần hoá đơn của provider. Bài ngoài profile hoặc thiếu bằng chứng tiếp tục vào đúng danh sách, không buộc duyệt mọi trường và không tự giảm ngưỡng cho qua.

Quy trình và lệnh vận hành: [STANDARDIZATION_PRODUCTION_WORKFLOW.md](STANDARDIZATION_PRODUCTION_WORKFLOW.md).

## 6. Bằng chứng

Thư mục riêng trên máy:

`D:\AI-Code\Build_HNCode\.local\openrouter-pilot\outputs\production_workflow_20261120`

- `tests-release-final.log`: lượt hồi quy cuối.
- `validation150.json`: phân luồng 150 fixture và danh sách ứng viên có code tác giả/full judge.
- `shadow-finalize.json`: 10 lần finalizer áp dụng rồi hoàn tác.
- `http-checks.json`: kiểm tra gateway.
- `migration-qa.log`, `migration-drift.log`: schema QA.

Backup DB và runtime nằm trong thư mục riêng, không đưa vào gói tài liệu công khai hoặc Git. Báo cáo không chứa API key hay thông tin đăng nhập.
