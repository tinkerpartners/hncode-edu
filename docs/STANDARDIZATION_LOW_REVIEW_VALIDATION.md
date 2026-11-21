# Báo cáo triển khai và kiểm thử chuẩn hoá bài tập

Ngày: 20/11/2026. Nhánh local: `codex/mrtee-hncode-standardization-v3`.

## 1. Kết luận

Đã triển khai cửa kiểm tra nguồn dùng chung, kho tách riêng theo fingerprint, chuẩn hoá đề bảo toàn bằng quy tắc, sửa lựa chọn proposal cũ, tách ngân sách AI khỏi apply và siết bằng chứng theo từng trường.

Đã cập nhật runtime QA trên ổ D, tại cùng cổng 8001. Không sửa bài production, không bật auto apply production và không gọi AI trả phí mới trong lượt kiểm thử này.

Kết quả giúp giảm việc duyệt nhờ tự xử lý phần chứng minh được và bỏ qua phần chưa đủ căn cứ. Chưa thể kết luận toàn bộ ngân hàng bài được tự chuẩn hoá hoàn chỉnh hoặc nội dung AI đúng tuyệt đối.

## 2. Kết quả kiểm thử

| Hạng mục | Kết quả |
| --- | --- |
| Bộ hồi quy chuẩn hoá, provider, phân quyền và home liên quan | **293/293 test đạt**, 80,561 giây ở lượt release |
| Kiểm tra lại các view sau cập nhật nhãn tiếng Việt | **8/8 test đạt** |
| Black và Ruff trên các file Python thay đổi | Đạt |
| `git diff --check` | Đạt |
| Migration `0285_standardization_routing` trên DB QA riêng | Áp dụng thành công |
| Đối chiếu 150 fixture đã lưu, không gọi AI | Hoàn thành |
| Thử auto apply nội dung rồi rollback trên 10 fixture thật | **10/10 thành công**; batch cap AI bằng 0 không cản apply |
| Bài tách riêng vượt gate auto apply trong mô phỏng | **0** |
| Bản cũ bị chọn khi đã có bản mới trong mô phỏng | **0** |
| Nguồn 150 fixture sau mô phỏng | Hash không thay đổi |
| Số proposal/decision/usage trước và sau mô phỏng rollback | Không thay đổi |
| Bài demo mới, chấm thật với checker `identical` | **AC 100/100**, hai test |
| HTTP qua gateway: trang chính, dashboard, kho tách riêng, chi tiết demo | **4/4 HTTP 200**; trang internal `private, no-store` |

293 test là số test trong bộ hồi quy phần mềm, không phải 293 bài được AI giải lại. Lượt đối chiếu 150 bài đọc lại kết quả đã lưu và kiểm tra nguồn, gate, parser, bảo toàn và thao tác ghi. Không chấm lại toàn bộ 150 chương trình hoặc chạy thêm 150 lượt AI.

Browser của chat không kết nối được, nên chưa kiểm tra ảnh chụp giao diện hoặc thao tác Safari thật ở lượt này. Các trang được render bằng Django client và kiểm tra HTTP qua gateway đang chạy.

## 3. Phân luồng 150 bài

| Nhóm | Số bài | Cách xử lý |
| --- | ---: | --- |
| Đủ nguồn và dữ liệu để tiếp tục xét từng phần | 114 | Không đồng nghĩa đã hoàn tất chuẩn hoá |
| Thiếu đề/nguồn đọc được | 16 | Để riêng |
| Chưa rõ ngữ nghĩa/quy tắc quyết định đáp án | 10 | Để riêng |
| Mâu thuẫn hợp đồng chấm | 6 | Để riêng |
| Cần luồng kiểm chứng chuyên biệt | 4 | Để riêng |
| Tổng | **150** | **36 bài tách riêng** |

Trong 114 bài qua cửa nguồn, parser/quy tắc mới tạo được **69 bản biến đổi bảo toàn**:

- **30** bản đạt `STRICT_OK`, gồm khung ví dụ chuẩn.
- **39** bản mới đạt `HEADINGS_OK`; phần bố cục còn lại giữ nguyên, ghi hoàn tất một phần khi áp dụng.

Nghiên cứu trước đó chỉ tìm được 41 ứng viên sửa heading bằng quy tắc cũ. Con số 69 thể hiện phạm vi biến đổi an toàn mở rộng trên đúng tập dữ liệu này; không phải bằng chứng 69 lời giải/tag/rating đã đúng hoặc đã được cập nhật vào 150 fixture gốc.

Các nội dung AI chưa có chứng minh tương đương, hướng dẫn chưa có xác nhận, tag/rating chưa có baseline được giữ lại. Không hạ ngưỡng confidence để làm đẹp tỷ lệ tự hoàn tất.

## 4. Kiểm tra trực tiếp trên local

- [Dashboard chuẩn hoá](http://localhost:8001/qa-standardization/internal/standardization/)
- [Kho bài thiếu căn cứ/dữ liệu](http://localhost:8001/qa-standardization/internal/standardization/?route=deferred_source&status=)
- [Demo bảo toàn khoảng trắng](http://localhost:8001/qa-standardization/internal/standardization/237/)

Dùng tài khoản QA đã được cấp quyền trước đó. Báo cáo không chứa mật khẩu hoặc API key.

Demo mới:

- Mã `qa_preserve_ws_v1`, ID 237, riêng tư.
- Đề có heading tiếng Việt, ví dụ chứa dấu cách đầu/cuối dòng và dòng trống.
- Proposal nội dung #1025 đã được áp dụng bằng quy tắc và đạt `STRICT_OK`.
- Verification #249, submission #134: AC 100/100 với checker `identical`; test thứ hai có cả tab và dòng trắng.
- Phần hướng dẫn chưa được tự áp dụng vì chưa có xác nhận nội dung độc lập, dù code AC. Đây là kết quả gate mong đợi.
- Policy thử được tắt sau khi xong. Không dùng demo để mở policy cho các bài khác.

150 fixture cũ, các kết quả AI và bằng chứng chấm trước đó được giữ nguyên để đối chiếu. DB chỉ thêm trạng thái phân luồng; demo dùng mã và dữ liệu riêng.

## 5. Những thay đổi dev cần chú ý

1. `readiness.py`: kiểm tra nguồn, hợp đồng, full test, ZIP/checker/generator và trạng thái đang tách riêng.
2. `format_v1.py`, `integrity.py`: chuẩn heading/khung ví dụ bằng quy tắc; bảo toàn khoảng trắng có nghĩa.
3. `policy.py`: chọn newest trước khi lọc pending/scope; kiểm tra dưới khoá; không dùng budget AI để cản apply; giới hạn hậu kiểm và dừng lỗi nghiêm trọng.
4. `verification.py`: phân biệt full judge với chứng cứ khác; gắn chứng cứ hướng dẫn với nội dung/code/test; không coi AC là bằng chứng đủ cho văn bản.
5. `routing.py`, model/migration mới: kết quả từng phần; không tính heading-only là chuẩn hoá hoàn tất.
6. Internal UI/admin: lọc kho tách riêng, hiển thị lý do tiếng Việt và trạng thái theo nhóm.
7. `prompts.py`: phiên bản v5 nhấn mạnh kiểm tra Solution/code tham khảo, tag quyết định và giới hạn của confidence/AC. Chưa chạy model thật với v5 trong lượt này; không suy ra chất lượng model mới từ test phần mềm.
8. Các gate chứng cứ, baseline và bảo toàn tag áp dụng cả đề xuất AI từ luồng cũ, không chỉ guided; tránh để luồng cũ đi vòng qua các giới hạn mới.

## 6. Điều kiện triển khai và phần tiếp theo

Chạy migration trước khi chạy web/worker mới. Giữ cờ `STANDARDIZATION_GUIDED_AUTO_APPLY` tắt khi dev kiểm tra; policy cần đúng phạm vi và phiên bản prompt. Đánh giá một lô hữu hạn trước khi mở rộng.

Chưa có verifier ngữ nghĩa tự động cho mọi hướng dẫn, baseline độc lập cho mọi tag/rating, reservation tổng ngân sách giữa nhiều worker hoặc cache AI theo từng thành phần. Vì vậy bản này phù hợp để tiếp tục chạy thử và tự xử lý phần bảo toàn/cấu hình đủ điều kiện; **chưa nên bật auto apply tất cả trường cho toàn bộ production**.

Không bắt buộc người dùng duyệt hết phần còn lại. Phần chưa đủ bằng chứng được giữ riêng theo thiết kế; việc bổ sung verifier cần đánh giá riêng trước khi cấp quyền tự áp dụng.

Chi tiết thuật toán gate, cấu hình, trạng thái và lệnh vận hành: [tài liệu triển khai](STANDARDIZATION_LOW_REVIEW_IMPLEMENTATION.md).

## 7. Bằng chứng lưu tại máy

Thư mục riêng trên D:

`D:\AI-Code\Build_HNCode\.local\openrouter-pilot\outputs\low_review_research_20261120`

- `validation-routing150.json`: phân luồng từng fixture và 10 giao dịch rollback.
- `demo-routing.json`: kết quả máy chấm thật và gate giữ riêng hướng dẫn.
- `http-routing.json`: kiểm tra gateway và giới hạn kiểm tra browser.
- `tests-routing-release.log`, `tests-views-release.log`: log hồi quy.
- `migration-qa.log`: migration QA.

Backup DB/runtime nằm trong thư mục riêng, không đưa vào Git hoặc gói tài liệu gửi dev. Mã nguồn và tài liệu nằm ở nhánh local nêu trên; chưa push hoặc tạo PR cho lượt sửa này.
