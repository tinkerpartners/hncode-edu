# Benchmark 100 bài mới — AI thật và máy chấm local

Lượt dữ liệu: 20/11/2026. Bộ 100 bài tách khỏi các đợt thử trước và bộ hồi quy 50 bài. Dữ liệu thực tế và kết quả ở ổ D, không commit ZIP/checker, key API hoặc tài khoản vào Git.

## Phạm vi

- 100 bài lấy từ các giai đoạn khác nhau, nhiều mức phổ biến, định dạng và thuật toán. Chọn theo các tầng kiểm thử, không chọn lại dựa trên kết quả AI.
- Nhóm chọn chính: 25 bài cơ bản/đề ngắn, 20 Markdown/thuật toán đa dạng, 20 có subtask hoặc thuật toán nâng cao, 15 nhập xuất file, 10 HTML/bảng/text cũ, 3 PDF, 3 ảnh, 3 nguồn thiếu/ngắn và 1 bảng. Các đặc trưng giao nhau: thực tế có 12 bài kèm PDF.
- Có 6 bài dạng chấm đặc biệt, checker Python/Testlib/số thực, 11 Solution gốc có code được trích nguyên và **phải chấm lại** trước khi dùng làm tham chiếu. Không lấy code bài nộp của học sinh.
- Pipeline thật: chuẩn hoá đề → kiểm tra tham chiếu → Luna sinh hướng dẫn/code/tag/rating → chấm thật → Luna sửa → Sol nếu cần và được phép. Không bật auto-apply.
- Prompt `std_prompts_v4`, effort `medium`; baseline code `3419e6e`. Phiên bản baseline được giữ nguyên suốt đợt để kết quả nhất quán. Các sửa đổi phát hiện trong benchmark được kiểm thử riêng, không trộn vào số liệu baseline.

## Kiểm soát thực thi và chi phí

- Chạy trên `localhost:8001`, DB `hncode_openrouter_pilot`, mã `qa100_*`, hàng đợi Redis/Celery riêng cho benchmark. Không ghi lên production.
- Nhóm đầu gồm 12 bài được chạy và kiểm tra kết nối AI/máy chấm/ghi chi phí trước khi xếp phần còn lại.
- API key chỉ truyền bằng stdin và nằm trong bộ nhớ tiến trình chạy thử. Không lưu key vào settings, biến môi trường container, source hoặc báo cáo.
- Trần toàn đợt 3 USD; mỗi bài/batch 0,25 USD. Trước mỗi request, bộ điều phối giữ trước chi phí tối đa dựa trên byte đầu vào, JSON schema, số token đầu ra tối đa và mức giá cao nhất của model được nhà cung cấp trả về trước đợt chạy.
- Chi phí request thực tế phải được báo đầy đủ để giải phóng phần dự phòng; nếu chi phí không rõ thì dừng toàn đợt. Không tự retry request mạng không rõ tình trạng tính phí trong benchmark.
- Chỉ hai request AI chạy đồng thời. Timeout của máy chấm không làm gọi model khác hoặc tạo bài nộp mới trong khi bài nộp cũ còn đang chờ.
- Kết quả từng lần gọi, ledger, hash nguồn/test/code và bài nộp local được giữ để tra cứu.

Đây là bộ điều phối đánh giá ngoài repository, không phải cam kết rằng mọi đợt production đã có cơ chế giữ trước chi phí. Cap production hiện có vẫn được kiểm tra trước request và có thể vượt phần còn lại bởi request đang chạy; cần đánh giá riêng trước khi mở rộng số worker production.

## Các lỗi được chặn sớm sau benchmark

### Nguồn chỉ có ví dụ

Một số bài chỉ có `Input`/`Output` và dữ liệu mẫu, không có yêu cầu. Baseline còn gọi AI rồi mới giữ `needs_clarification`. Bộ phân loại hiện nhận diện một trường hợp hẹp: ngoài các cặp fence ví dụ chỉ còn nhãn Input/Output/Example/Test/Ví dụ.

- Chuyển sang thiếu nguồn trước khi gọi AI.
- Kiểm tra trên Markdown gốc trước khi chuyển thành text hiển thị để không mất ranh giới fence.
- Vẫn có thể dùng PDF hoặc bản dịch đầy đủ làm nguồn thay thế.
- Không đánh dấu thiếu nguồn chỉ vì bài ngắn, không có ví dụ hoặc có hàm phụ trong code.
- Không tự suy ra yêu cầu từ dữ liệu mẫu.

### Đề chỉ cho viết hàm nhưng cấu hình chấm là chương trình

Hai bài trong tập có yêu cầu rõ “không được viết hàm main”, cùng khai báo hàm cần triển khai, nhưng cấu hình thực tế là chương trình stdin/stdout. Baseline đã tốn lượt sinh/sửa trước khi AI báo mâu thuẫn.

`read_contract()` hiện yêu cầu **đồng thời** có quy tắc cấm main rõ ràng và một khai báo hàm:

- Không có metadata: xác định đây là dạng hàm và chuyển sang luồng cần kiểm chứng chuyên biệt.
- Metadata/init.yml là stdin/file: ghi `statement_judge_task_kind_conflict`, dừng trước content AI và sinh code.
- Metadata đã là hàm/tương tác/output-only: giữ cơ chế chặn dạng đặc biệt sẵn có.
- Chỉ tên bài chứa “tương tác” hoặc việc xuất hiện hàm phụ không đủ để thay đổi giao diện chấm.
- `current_full_verification()` cũng kiểm tra hợp đồng hiện tại, vì bằng chứng cũ không được phép vượt qua một mâu thuẫn giao diện đã xác định.

Không chỉnh cấu hình production để ép code chạy và không tự chuyển bài viết hàm thành chương trình có main.

## Cách đọc số liệu

Phân biệt các nhóm sau:

1. Code được sinh bởi Luna và đạt ngay.
2. Code đạt sau sửa Luna hoặc chuyển Sol.
3. Hướng dẫn dùng code Solution gốc đã được chấm lại.
4. Code chưa đạt trên bộ test hiện tại.
5. Bài bị giữ do nguồn hoặc hợp đồng chấm, không được quy thành AC.
6. Đề/diễn giải/tag/rating chưa đủ căn cứ dù code đã AC.

AC chỉ chứng minh đạt bộ test hiện có. Kiểm tra độc lập của một số bài cũng không thay thế đánh giá toàn bộ 100 đề và diễn giải. Độ khó là ước lượng, không phải rating Codeforces chính thức nếu chưa đối chiếu nguồn.

Báo cáo số liệu cuối, kết quả từng bài, ca sửa, chi phí theo model/bước và các trường hợp cần bổ sung nằm ở `D:\AI-Code\Build_HNCode\outputs\benchmark100_20261120\`.

## Kiểm tra local

- Trang theo dõi: <http://localhost:8001/qa-standardization/?suite=benchmark100>.
- Bộ 50 bài cũ: <http://localhost:8001/qa-standardization/?suite=recheck50>.
- Tài khoản chỉ xem: thông tin đăng nhập ở `.local/openrouter-pilot/THONG_TIN_DANG_NHAP_QA.md` trên ổ D. Không đưa file này lên Git.
- Người xem không có quyền sửa đề/approve/apply. PDF local được phục vụ qua route QA có kiểm tra quyền.
- HTTP smoke kiểm tra đủ 100 dòng, 100 trang đề, 100 trang đối chiếu, trang chủ chính và bộ hồi quy cũ.

## Kiểm thử code

```sh
python manage.py test judge.tests judge.tests.test_home_layout --pattern='test_standardization*.py' --keepdb --noinput
python manage.py makemigrations --check --dry-run
```

Có hồi quy cho chặn nguồn chỉ có ví dụ, bản dịch thay thế, nhận diện hàm theo điều kiện rõ ràng, không nhận diện sai hàm phụ, và từ chối bằng chứng AC cũ khi hợp đồng chấm hiện tại mâu thuẫn. Không thay đổi schema DB.

Trước khi áp dụng production: review các bản vá, khởi động lại web/worker cùng phiên bản, giữ policy tắt hoặc giới hạn phạm vi. Không có policy production hoặc dữ liệu bài thật nào được thay đổi bởi benchmark này.
