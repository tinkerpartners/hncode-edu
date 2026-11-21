# Báo cáo kiểm thử chuẩn hoá bằng AI thật trên local

Ngày: 20/11/2026. Nhánh review: `codex/mrtee-hncode-standardization-v3`.

**Kết luận:** luồng tự áp dụng đã chạy được trên local với AI và máy chấm thật. Phần đủ điều kiện được cập nhật riêng; phần thiếu căn cứ hoặc bị phản biện được giữ lại. Có thể chuẩn bị triển khai theo phạm vi giới hạn sau khi chủ dự án duyệt. Chưa đủ bằng chứng để bật tự áp dụng không giới hạn cho toàn bộ ngân hàng bài.

Báo cáo được gửi kèm PR để dev lead review trước khi merge và triển khai. **Chưa deploy production**. Các số liệu dưới đây thuộc DB QA riêng trên máy local, không phải thay đổi trên HNCode thật.

## 1. Phạm vi và cách thử

Đã tạo **22 bản sao riêng** từ dữ liệu bài thực tế đã có trên local. Mã bắt đầu bằng `qa_lr1_`, ID 238–259; nhóm `qa_live_review1`. Giữ nguyên 150 fixture trước đó và kiểm tra lại hash các bài nguồn sau khi chạy.

| Nhóm thử | Số trường hợp | Mục tiêu |
| --- | ---: | --- |
| Có code tác giả làm căn cứ | 9 | Chấm lại code, phản biện lời giải/tag/Points, xét tự áp dụng |
| Code đã qua test nhưng thiếu căn cứ tác giả phù hợp | 3 | Không tự coi AC là đủ điều kiện chứng nhận lời giải |
| Thiếu đề, nguồn mơ hồ, hợp đồng nhập xuất mâu thuẫn hoặc chấm đặc biệt | 6 | Để riêng trước bước sinh/phản biện không cần thiết |
| Cố tình cài lời giải sai hoặc tag sai, có ghi nhãn kiểm thử | 2 | Kiểm tra khả năng chặn sai thay vì chỉ thử các ca thuận lợi |
| Sinh mới từ đầu bằng prompt hiện hành | 2 | Luna viết → máy chấm local → Sol phản biện → xét áp dụng |
| **Tổng** | **22** | |

Trong 20 trường hợp đầu, bản nháp người viết được tái sử dụng từ các lần chạy đã lưu; máy chấm và phản biện Sol được chạy thật trong đợt này. Hai trường hợp cuối dùng **Luna sinh mới với `std_prompts_v5`**, không tái sử dụng bản nháp cũ.

Reviewer dùng `independent_reference_review_v2`; model viết và model phản biện khác nhau. Test ẩn không được gửi cho AI. Các lời gọi trả phí chỉ được bật trong tiến trình thử có phạm vi; policy thử đã tắt sau khi hoàn tất.

## 2. Kết quả cuối cùng sau đối chiếu

| Trạng thái toàn bài | Số bài | Ý nghĩa |
| --- | ---: | --- |
| `auto_done` | **4** | Các nhóm bắt buộc đã đạt điều kiện hiện hành; cấu hình đúng sẵn được xác minh và giữ nguyên |
| `auto_partial` | **12** | Chỉ áp dụng nhóm đủ điều kiện; các nhóm còn lại giữ nguyên và được phân luồng |
| `deferred_source` | **6** | Thiếu hoặc mâu thuẫn căn cứ; không áp dụng nhóm trường nào |

Có **16/22** trường hợp có kết quả full judge còn hiệu lực. Đây không phải 16 bài đã đạt toàn bộ chuẩn hoá: full judge kiểm tra code, còn đề, diễn giải, tag và độ khó có cửa kiểm tra riêng.

Số lần áp dụng thực tế theo nhóm: **đề 14; lời giải 8; tag 5; Points 8; cấu hình 6**. Cấu hình đúng sẵn không bị tạo thao tác ghi giả để tăng bộ đếm. Tổng cộng **21 nhóm AI đã áp dụng** thuộc lời giải/tag/Points được kiểm tra lại bằng chính điều kiện phản biện độc lập và đều đạt.

Hai mẫu kiểm soát âm đều bị chặn đúng:

- `qa_lr1_sumall_badsol`: lời giải cố tình mô tả sai phép toán/độ phức tạp, trong khi code tham khảo vẫn AC. **Không áp dụng lời giải**.
- `qa_lr1_tonghaiso_2_badtag`: cố tình gán quy hoạch động cho bài cộng hai số. **Không áp dụng tag sai**; phần lời giải và Points có căn cứ vẫn được xử lý riêng.

Các ví dụ lỗi tự nhiên được phản biện giữ lại:

- `19_hsg12_hanoi_bai4`: lập luận đếm thiếu nhóm ban đầu; code AC không tự chứng minh đoạn giải thích đúng.
- `ext11_bangxep`: lập luận về thứ hạng chưa xử lý đúng các trường hợp bằng nhau.
- `cfbonus_1294c_fresh`: bản Luna sinh mới đảo chiều quan hệ chia hết trong diễn giải và thiếu bước chứng minh lựa chọn thừa số. Lời giải được giữ lại, chỉ áp dụng phần đề bảo toàn đủ căn cứ.
- `loinguyenhuyetthongd`: có cảnh báo thận trọng về đệ quy/stack, cần đối chiếu môi trường thực thi; chưa kết luận đây là lỗi thuật toán. Bài cũng nằm ngoài phạm vi độ khó của profile tự áp dụng đang thử.

Không dùng tỷ lệ 4/22 để đo độ chính xác AI hoặc dự báo tỷ lệ hoàn tất của toàn hệ thống: tập này được chọn có chủ đích để chứa ca khó, dữ liệu thiếu và lỗi cài sẵn. Chưa có bộ nhãn chuẩn độc lập cho toàn bộ 22 bài để tính tỷ lệ sai ngoài thực tế.

## 3. Chi phí thực tế

| Lượt chạy | Lời gọi thành công | Chi phí USD |
| --- | ---: | ---: |
| 20 ca tái sử dụng bản nháp | 11 Sol | 0.136914 |
| 2 ca sinh mới hoàn chỉnh | 2 Luna + 2 Sol | 0.025135 |
| **Tổng đợt này** | **15** | **0.162049** |

Trần thử là **1,50 USD**. Chi phí trên là phần phát sinh của đợt này, không bao gồm tiền sinh các bản nháp lịch sử. Không lấy chi phí trung bình trên 22 ca để nhân thẳng cho toàn ngân hàng vì sáu ca dừng sớm và 20 ca không sinh mới.

Một request ban đầu bị provider từ chối schema trước suy luận; đã đối chiếu lỗi HTTP 400, probe tái hiện và usage không tăng để xác nhận chi phí 0 USD, có lưu audit. Không tự coi mọi lỗi mạng/request lỗi là miễn phí. Các reservation của lượt thử đã được đối chiếu, không còn khoản chưa rõ do lượt thử này để lại.

Gọi lại finalizer trên bài đã hoàn tất: **0 request AI mới**. Giữ lại bản nháp, kết quả chấm và phản biện còn hiệu lực giúp tránh trả tiền lặp lại.

## 4. Lỗi tìm thấy và đã sửa

| Vấn đề phát hiện qua chạy thật | Cách sửa | Kiểm chứng |
| --- | --- | --- |
| Provider từ chối keyword `uniqueItems` trong strict JSON schema | Chuyển schema gửi đi sang tập keyword tương thích; vẫn kiểm tra đầy đủ schema gốc tại local, không bỏ quy tắc duy nhất/giới hạn | Tái hiện HTTP 400 trước sửa; 13 request Sol thành công sau sửa; test schema/property names |
| Tên setting trần tổng không thống nhất giữa tài liệu và code | Nhận cả `STANDARDIZATION_BUDGET_USD_TOTAL` và biến tương thích `_PER_TOTAL`; nếu có cả hai dùng trần thấp hơn | Test riêng canonical setting và hai trần khác nhau |
| Lỗi hạ tầng phản biện bị phân luồng lại thành vấn đề nội dung | Lưu lỗi kèm đúng phiên bản chứng cứ; chỉ dùng khi vẫn khớp; xoá sau phản biện thành công | Test lưu lỗi, phân loại và xử lý chứng cứ đổi |
| Cấu hình đúng sẵn vẫn làm toàn bài chưa hoàn tất | Xác minh profile bằng quy tắc và ghi `already_standard`, không tạo apply giả | Test trạng thái hoàn tất và cấu hình không đổi |
| Tag không đổi bị tính là đã chứng nhận dù critic không chấp thuận | Profile yêu cầu phản biện không được đi vòng qua fallback no-op/hiệu chuẩn | Test từ chối; hậu kiểm lại toàn bộ nhóm AI đã áp dụng |

Trong lượt đầu có **một proposal tag no-op bị ghi trạng thái áp dụng chưa đúng**. Giá trị tag trước/sau hoàn toàn giống nhau; đã khôi phục trạng thái qua cơ chế audit, giữ nguyên tag giáo viên. Kết quả cuối trong báo cáo này đã trừ proposal đó. Nhật ký ban đầu và bằng chứng khắc phục được giữ riêng, không xoá lịch sử lỗi.

Trạng thái hoàn tất trên trang chi tiết còn đối chiếu hash nguồn đầy đủ và fingerprint hiện tại, tránh dùng nhãn hoàn tất cũ sau khi dữ liệu đã đổi. Giao diện hàng đợi hiển thị “Đã đúng chuẩn” cho cấu hình đúng sẵn.

## 5. Kiểm thử phần mềm, code và web

- **325/325 test hồi quy đạt**, 74,350 giây, DB test riêng. Sau cập nhật phần hiển thị hàng đợi: **9/9 test view đạt**, 3,020 giây. Hai lượt có phần trùng nhau, không cộng thành 334 test khác nhau.
- Black kiểm tra chín file Python thay đổi, Ruff toàn repository và `git diff --check` đều đạt.
- **660/660 phép thử bổ sung** cho bốn code mẫu: `cfbonus_1294c` 21, `lt11_khoichuso` 259, `sumall` 205, `tonghaiso_2` 175. Dùng bộ sinh/kiểm tra độc lập, vét cạn nhỏ và biên; không phải toàn bộ ngân hàng bài.
- Kiểm tra khôi phục proposal trong giao dịch: khôi phục khớp dữ liệu trước, sau rollback vẫn giữ bản demo đã áp dụng để người dùng xem.
- HTTP qua gateway 8001: bảy nhóm vấn đề, danh sách 22 ca và năm trang chi tiết đều HTTP 200, `private, no-store`; khách chưa đăng nhập bị chuyển đến đăng nhập; trang chính local vẫn HTTP 200.
- Runtime QA và worker đã cập nhật các file ứng dụng mới trên ổ D. Không thêm cổng 8002; không sửa runtime chính/DB chính.
- Chưa kiểm tra trực quan bằng Safari hoặc trình duyệt thật trong đợt này. Kiểm tra HTTP/render không thay thế kiểm tra giao diện trên thiết bị.

## 6. Dữ liệu demo để chủ dự án kiểm tra

Đăng nhập bằng tài khoản QA local đã được bàn giao trước đó. Các link sau nằm trong ứng dụng QA, DB `hncode_openrouter_pilot`:

- [Danh sách 22 trường hợp](http://localhost:8001/qa-standardization/internal/standardization/?route=all&status=&q=qa_lr1_).
- [Ca hoàn tất: Tích ba số phân biệt](http://localhost:8001/qa-standardization/internal/standardization/238/).
- [Ca hoàn tất: Khối chữ số, sinh mới từ đầu](http://localhost:8001/qa-standardization/internal/standardization/259/).
- [Lời giải cố tình sai, phải bị giữ lại](http://localhost:8001/qa-standardization/internal/standardization/256/).
- [Tag cố tình sai, phải bị giữ lại](http://localhost:8001/qa-standardization/internal/standardization/257/).
- [Lời giải mới sinh có lỗi diễn giải, phải bị giữ lại](http://localhost:8001/qa-standardization/internal/standardization/258/).

Các proposal, phản biện, kết quả chấm và lịch sử áp dụng được giữ để kiểm tra. Hai bài cài lỗi là bản sao có nhãn kiểm thử, không phải nội dung thay vào bài thật. Policy thử đã tắt; worker không được để key hoặc cờ tự chạy AI từ thử nghiệm.

## 7. Đề xuất bước tiếp theo sau khi duyệt

1. Dev lead review PR kèm báo cáo này, kiểm tra code và kế hoạch migration trước khi merge.
2. Backup DB/file bài trên VPS; deploy code và migration, khởi động lại web/worker với cờ AI tự áp dụng còn tắt. Không mang DB QA hoặc các tài khoản demo lên production.
3. Kiểm tra health, phân quyền, source/test path và kết nối judge tại VPS. Cấu hình key qua secret/settings tại máy chủ; cấu hình ceiling, ngân sách và policy giới hạn ID/batch.
4. Chạy lô đầu **20–50 bài thật** có nguồn/test rõ, xem kế hoạch trước enqueue. Phần nào đủ cửa thì tự áp dụng; phần thiếu dữ liệu hoặc bất đồng vào danh sách riêng. Hậu kiểm lấy mẫu và kiểm tra toàn bộ lỗi/ngoại lệ, không yêu cầu duyệt tay mọi bài.
5. Tăng dần theo lô sau khi số liệu production ổn định. Sai mẫu thì dừng policy và dùng lịch sử snapshot để kiểm tra/khôi phục đúng nhóm; không nới điều kiện hoặc hạ model âm thầm để tăng số bài hoàn tất.

Không cần chạy lại toàn bộ lô AI local nếu không có sửa logic/prompt hoặc lỗi mới. Nhưng vẫn cần smoke test tại môi trường VPS và một lô production giới hạn, vì filesystem, judge, quyền và dữ liệu thật có thể khác local.

Hướng dẫn cấu hình/CLI và giới hạn profile: [STANDARDIZATION_PRODUCTION_WORKFLOW.md](STANDARDIZATION_PRODUCTION_WORKFLOW.md).

## 8. Bằng chứng và gói bàn giao

Thư mục bằng chứng nội bộ:

`D:\AI-Code\Build_HNCode\.local\openrouter-pilot\outputs\local_live_review_20261120`

Nguồn số liệu cuối là `audit-final.json` (22 ca, sau sửa trạng thái), `fresh-summary.json` (chi phí cộng dồn), `tests-final.log`, `tests-views-final.log`, `independent-oracles.json` và `http-checks.json`. `summary.json` là ảnh chụp của lượt 20 ca trước hậu kiểm trạng thái, không dùng thay audit cuối.

Gói gửi dev nằm tại:

`D:\AI-Code\Build_HNCode\outputs\standardization_live_local_20261120`

Gói gồm báo cáo, tổng hợp trạng thái cuối, bảng 22 ca, kết quả test và HTTP/oracle. Không đưa API key, mật khẩu, backup DB hoặc source archive vào Git/gói báo cáo.
