# Chạy chuẩn hoá thật: tự áp dụng từng phần và duyệt theo vấn đề

Ngày cập nhật: 20/11/2026. Tài liệu này bổ sung và thay thế các giới hạn tương ứng trong báo cáo triển khai trước về `human_review`, hiệu chuẩn cố định và reservation.

## 1. Hành vi sau cập nhật

Khi một lượt guided hoàn tất, hệ thống tự xét các policy đã bật cho đúng bài. Phần đủ điều kiện được áp dụng bằng cơ chế snapshot, kiểm tra xung đột và audit hiện có. Phần chưa đủ điều kiện giữ nguyên và vào danh sách tương ứng; không buộc người dùng duyệt cả bài.

Kết quả chấm đến muộn cũng có tác vụ hoàn tất riêng. Khi hết số lần theo dõi, hệ thống ghi `judge_poll_exhausted` vào nhóm hạ tầng và giữ ID bài nộp. Nút kiểm chứng có thể lấy lại kết quả từ bài nộp đó khi dữ liệu vẫn khớp. CLI `standardize_finalize` cho phép tiếp tục xét các lượt đã có kết quả mà không tạo lại bản nháp.

Kiểm tra nguồn đọc cấu hình và danh mục ZIP. Máy chấm đọc dữ liệu từng test khi kiểm chứng code. Dấu vân tay bộ test đọc tối đa 8 MiB dữ liệu file mỗi lần kiểm tra. Các file vượt giới hạn dùng phiên bản filesystem gồm kích thước, mtime, ctime, thiết bị và inode. Cần mount cùng dữ liệu test với thông tin phiên bản nhất quán cho web và worker. Khi nâng cấp, chứng cứ dựa trên hash byte của file lớn có thể cần chấm lại. Thay dữ liệu qua ghi hoặc thay thế file thông thường làm đổi phiên bản và vô hiệu kết quả cũ.

Không tự tạo hoặc bật policy production. Các cờ triển khai vẫn cần cấu hình rõ ràng. Mặc định mới không làm thay đổi các bài trên production khi deploy code.

## 2. Chứng cứ tự áp dụng mới

Ngoài xác nhận thủ công hoặc baseline hiệu chuẩn hiện có, có thêm profile **phản biện độc lập kết hợp code tác giả đã kiểm chứng**. Profile này cho phép một nhóm bài tự áp dụng mà không phải chờ tích đủ 100 bài đã duyệt trước.

| Cửa kiểm tra | Điều kiện |
| --- | --- |
| Nguồn | Qua kiểm tra chung về đề, hợp đồng chấm và full test; không có cảnh báo ngữ nghĩa đang hiệu lực |
| Code tham khảo | Đúng bản `ProblemSolutionCode` của bài, expected AC; code và ngôn ngữ khớp từng byte với bản được dùng |
| Máy chấm | Chính code đó đã đạt full test, đúng phiên bản đề/test/cấu hình hiện tại; không nhận nhãn AC lịch sử thay việc chấm lại |
| Phản biện | Một model khác model viết lời giải, nằm trong danh sách được policy cho phép, đọc đề và nội dung/code thực tế |
| Nội dung | Tất cả kiểm tra về mô hình bài, thuật toán, lập luận, độ phức tạp, giới hạn và biên đều đạt; phản biện không được tự thực thi code |
| Phạm vi độ khó | Khoảng đánh giá độc lập nằm trong 800–1400, rộng tối đa 200; policy chỉ được siết thêm phạm vi này |
| Hậu kiểm | Tỷ lệ lấy mẫu ít nhất 10%; daily cap không quá 200 thao tác áp dụng; sai một mẫu trong profile này sẽ dừng policy ngay |

Đây là chính sách chấp nhận rủi ro có giới hạn, **không phải chứng minh hình thức hoặc cam kết đúng tuyệt đối**. Code tác giả, full test và phản biện là các nguồn kiểm tra khác nhau; hai model đồng ý tự nó không đủ. Bộ test thiếu trường hợp quan trọng vẫn có thể bỏ sót lỗi.

Các bài không có code tác giả phù hợp, bài khó hơn phạm vi trên hoặc có độ khó chưa rõ vẫn được tạo lời giải/chấm theo luồng hiện có, nhưng không được tự thông qua bằng profile này. Không hạ ngưỡng để tăng tỷ lệ hoàn tất.

### Xét riêng từng nhóm trường

- **Hướng dẫn:** có thể tự áp dụng khi qua toàn bộ cửa profile; không còn bắt buộc `human_review` cho nhóm này. Những trường hợp ngoài profile tiếp tục giữ riêng.
- **Tag:** phải ánh xạ được toàn bộ tag, không còn tag chưa phân giải; danh sách đề xuất phải khớp danh sách kỹ thuật mà phản biện chấp nhận. Chỉ bổ sung, không xoá tag giáo viên.
- **Points:** khoảng đánh giá của người viết và phản biện đều phải hẹp, giao nhau và chứa giá trị đề xuất. Một phản biện chỉ đồng ý hướng dẫn nhưng không đồng ý Points sẽ không chặn phần hướng dẫn.
- **Đề/cấu hình:** vẫn theo chứng minh bảo toàn và profile cấu hình hiện có; không dùng AI phản biện để cho phép viết lại đề tùy ý.

Cấu hình đã khớp profile được xác minh bằng quy tắc và ghi `already_standard`, không tạo proposal áp dụng giả. Tag AI giữ nguyên giá trị cũng không được tính là đã chứng nhận nếu profile phản biện bắt buộc không thông qua; giữ tag giáo viên và để riêng việc chứng nhận.

Từ v3, `STANDARDIZATION_GUIDED_AI_REWRITE` mặc định `False`. Guided thử định dạng bằng quy tắc trước. Nếu chưa chứng minh được bảo toàn, hệ thống giữ nguyên đề và đưa nhóm nội dung vào danh sách cần kiểm tra. Bật cờ này để tạo bản nháp AI trong tình huống đó. Bản nháp vẫn phải qua các kiểm tra hiện hành khi áp dụng.

Rating là ước lượng theo thang Codeforces, không trở thành rating chính thức chỉ vì có hai đánh giá gần nhau.

## 3. Định danh chứng cứ và tính lặp lại

Chứng cứ phản biện gắn với hash của đề, cấu hình/test, toàn bộ payload lời giải, ID/hash payload tag và Points, model viết, model phản biện và phiên bản review.

- Nội dung đổi trong lúc chờ AI: ghi chi phí thực tế nhưng không cấp chứng cứ cho bản đã đổi.
- Có bản lời giải/tag/Points mới: chứng cứ cũ không tự dùng cho bản mới.
- Code tác giả bị sửa hoặc đổi expected result: chứng cứ mất hiệu lực.
- Chấm thất bại trên đúng phiên bản hiện tại hoặc đang chờ kết quả: không tự nộp lại liên tục khi gọi finalizer.
- Kết quả phản biện, kể cả không đạt, được tái dùng theo fingerprint. Không mua lại cùng một phản biện mỗi lần policy sweep.
- Lỗi request phản biện đã phát sinh được lưu; không tự gọi lặp lại khi chưa xử lý được lý do/chi phí.

Khi áp dụng cấu hình làm thay đổi điều kiện chấm, hệ thống kiểm tra lại code trước khi xét hướng dẫn/tag/Points. Không coi AC dưới giới hạn cũ là AC dưới giới hạn mới.

## 4. Danh sách theo vấn đề

Dashboard internal có các bộ lọc riêng:

| Danh sách | Ví dụ |
| --- | --- |
| Đề/nguồn | Thiếu đề, PDF/ảnh chưa đọc được, quy tắc quyết định đáp án chưa rõ |
| Bộ test/hợp đồng chấm | Thiếu full test, thiếu checker/generator, nhập xuất file mâu thuẫn |
| Bố cục đề | Chưa chứng minh được biến đổi bảo toàn hoặc chỉ sửa được heading |
| Hướng dẫn | Thuật toán/lập luận chưa đủ chứng cứ hoặc ngoài phạm vi tự áp dụng |
| Tag | Tag chưa ánh xạ, kỹ thuật đề xuất không được kiểm tra chấp nhận |
| Độ khó | Khoảng đánh giá rộng, hai đánh giá không chứa cùng giá trị đề xuất |
| Cấu hình | Thiếu runtime, judge hoặc tài khoản tác giả cần bổ sung |
| Máy chấm/dịch vụ/ngân sách | Timeout, request chưa rõ chi phí, thiếu cấu hình giá, không kết nối được provider |
| Policy | Chờ policy, quota áp dụng hoặc điều kiện vận hành; không phải lỗi nội dung |

Danh sách có phân trang, tìm tên/mã bài, nhóm trường, lý do và hành động cần làm. Các trường đang `needs_clarification` vẫn được đưa vào danh sách dù không đủ điều kiện policy sweep. Một bài có thể xuất hiện ở nhiều nhóm vấn đề, nhưng số bài trong từng nhóm được đếm riêng biệt, không cộng trực tiếp để ra tổng bài.

Bài bị chặn toàn bộ vì nguồn/test được ưu tiên đưa vào danh sách nguồn/test, tránh kéo admin đi duyệt tag/rating trước khi có dữ liệu gốc. Lỗi hạ tầng được giữ riêng khỏi yêu cầu duyệt nội dung.

## 5. Ngân sách khi chạy nhiều worker

`StandardizationReservation` giữ chỗ trước request. Một dòng khoá DB dùng chung tuần tự hoá bước nhận request; không giữ khoá khi gọi mạng.

Ngân sách đang sử dụng = chi phí đã ghi nhận + tổng khoản đang giữ/chưa rõ chi phí. Hai worker không cùng nhận request dựa trên một phần ngân sách còn lại. Nội dung request giống nhau đang chạy cho cùng bài cũng không được gửi trùng.

Ceiling do quản trị viên cấu hình là **ước tính bảo thủ cho một request**, không phải trần hoá đơn do provider bảo đảm. Cần đặt cao hơn chi phí tối đa dự kiến với số token request, theo giá đang sử dụng và ngân sách thật.

| Trạng thái | Hành vi |
| --- | --- |
| `held` | Request đang chạy hoặc worker chưa ghi xong kết quả; chưa tự trả ngân sách |
| `settled` | Đã có chi phí thực tế, giải phóng giữ chỗ và tính vào ledger |
| `uncertain` | Lỗi/timeout hoặc không có giá; tiếp tục giữ chỗ, không tự coi là miễn phí |
| `overrun` | Chi phí thực tế vượt ceiling; dừng nhận AI request mới cho đến khi quản trị viên kiểm tra |

Khoản chưa rõ từ ngày trước vẫn tính khi nhận request mới; đổi ngày không tự trả ngân sách. Nếu worker chết trước khi báo kết quả, khoản giữ chỗ không tự hết hạn thành ngân sách trống.

Sau khi đối chiếu billing, người có quyền apply có thể xác nhận chi phí bằng CLI. Lệnh ghi người xác nhận, lý do, số tiền, trạng thái trước và thời điểm; không tự gọi AI và không tự sửa số tiền đã được provider ghi rõ sang một giá trị khác.

```sh
python manage.py standardize_reservation --id 123 --actual-usd 0.012345 --actor MrTee --note "Đã đối chiếu request trong billing provider, mã ..."
```

Với hàng `held` còn sót lại, chỉ dùng lệnh sau khi hàng đã tồn tại ít nhất một giờ, đã xác nhận worker và request không còn chạy, đồng thời đã xác minh chi phí thực tế. Nếu chưa rõ khoản phí thì giữ nguyên reservation.

```sh
python manage.py standardize_reservation --id 123 --actual-usd 0.012345 --actor MrTee --confirm-worker-stopped --note "Worker đã dừng. Đã đối chiếu request với bản ghi billing ..."
```

Khi xoá bài hoặc batch, ledger và reservation vẫn được giữ cho đối chiếu tổng ngân sách. Khoản vượt ceiling vẫn dừng nhận request AI mới trên toàn hệ thống đến khi được đối chiếu.

Reservation bảo vệ đường gọi đồng bộ `call_json`, gồm guided và phản biện mới. API Message Batch có luồng riêng; chưa được xem là hỗ trợ reservation bởi thay đổi này.

## 6. Cấu hình triển khai

Chạy migration `0285` đến `0288`, khởi động lại web/worker, rồi cấu hình local settings. Đặt key trong cấu hình riêng của môi trường.

```python
STANDARDIZATION_RESERVATIONS_ENABLED = True
STANDARDIZATION_BUDGET_USD_TOTAL = "<tran-tong-USD>"
STANDARDIZATION_REQUEST_USD_CEILINGS = {
    # Điền ceiling phù hợp theo giá và giới hạn token thực tế.
    "openrouter/<model-viet>": "<USD-moi-request>",
    "openrouter/<model-phan-bien>": "<USD-moi-request>",
    "openrouter/<model-sua-bai>": "<USD-moi-request>",
}
STANDARDIZATION_INDEPENDENT_REVIEW = True
STANDARDIZATION_REVIEW_MODEL = "<model-phan-bien>"
STANDARDIZATION_GUIDED_AUTO_APPLY = True
```

Mẫu `thresholds_json` cho **policy giới hạn phạm vi**:

```json
{
  "reference_review": {
    "enabled": true,
    "reviewer_models": ["<model-phan-bien>"],
    "max_rating": 1400
  },
  "audit_max_pending": 100
}
```

Policy phải chọn đúng prompt version, ID range, nhóm trường và có daily/total cap. Lô đầu nên lấy mẫu hậu kiểm cao, rồi mới giảm về mức phù hợp sau đánh giá thực tế. Không dùng một policy không giới hạn phạm vi để mở toàn bộ ngân hàng ngay sau deploy.

Các thao tác:

```sh
# Lập kế hoạch trước, chưa có chi phí hoặc ghi bài:
python manage.py standardize_guided --actor MrTee --from-id 1 --through-id 100 --limit 100
# Enqueue khi đã cấu hình provider, ngân sách, policy và phạm vi:
python manage.py standardize_guided --actor MrTee --from-id 1 --through-id 100 --limit 100 --enqueue
# Tiếp tục xét kết quả cũ: mặc định chỉ lập kế hoạch:
python manage.py standardize_finalize --actor MrTee --codes aplusb,threepoint
# Có thể chấm lại/phản biện/apply theo policy, không sinh lại toàn bộ nháp:
python manage.py standardize_finalize --actor MrTee --codes aplusb,threepoint --execute
```

Nếu không bật cờ tự áp dụng hoặc không có policy phù hợp, finalizer chỉ cập nhật phân luồng. Không tự sửa đề bằng cách vượt cờ triển khai.

## 7. Kiểm thử và phạm vi chứng minh

Test phần mềm kiểm tra quyết định tự áp dụng, trường hợp từ chối, chứng cứ cũ, thay đổi dữ liệu lúc chờ AI, lỗi mạng, dedupe, phân quyền, danh sách từng vấn đề và hai kết nối DB tranh ngân sách.

Các phản hồi reviewer trong unit test là giả lập có kiểm soát. Chúng chứng minh logic kiểm soát hoạt động đúng với đầu vào đó; **không chứng minh model phản biện ngoài thực tế có độ chính xác tương ứng**. Cần chạy một lô phản biện thật có hậu kiểm trước khi mở rộng profile tự áp dụng.

Đã kiểm thử bằng AI thật riêng trên local: xem [STANDARDIZATION_LIVE_LOCAL_REPORT.md](STANDARDIZATION_LIVE_LOCAL_REPORT.md). Chưa bật policy production hoặc cập nhật bài trên website thật. Reviewer protocol hiện dùng `independent_reference_review_v2`; schema gửi provider tương thích strict JSON, còn schema gốc vẫn kiểm tra đầy đủ tại local. Setting trần tổng chuẩn là `STANDARDIZATION_BUDGET_USD_TOTAL`; nếu cấu hình thêm biến tương thích `STANDARDIZATION_BUDGET_USD_PER_TOTAL`, hệ thống dùng trần thấp hơn.

Sau review PR #166, 331 test hồi quy trên DB local đạt. Các hook pre-commit áp dụng cho file trong PR đều đạt. Migration `0288` đã thử lùi và áp dụng lại trên DB test. Trên ba archive 109–354 MiB ở local, kiểm tra nguồn khi cache trống mất 59–95 ms. Hai phép đo riêng của cách cũ mất 2,34–6,46 giây để kiểm tra CRC toàn archive và 0,92–2,87 giây để tính SHA-256 toàn file.
