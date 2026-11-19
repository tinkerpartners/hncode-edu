# Chuẩn hoá tự động v2 — vận hành số lượng lớn

## Mục tiêu và nguyên tắc

Giảm số lượt gọi AI và số bài phải duyệt, đồng thời giữ bằng chứng độc lập cho từng nhóm trường. Không coi code AC là bằng chứng bản đề, chứng minh thuật toán, tags và độ khó đều đúng. Tự động áp dụng bằng policy có giới hạn và hậu kiểm mẫu; không suy đoán phần đề thiếu để tăng tỷ lệ hoàn thành.

Không có migration mới. Prompt/schema mới mang phiên bản `std_prompts_v3`; bản nháp và kết quả kiểm chứng v1 không bị ghi đè.

## Luồng xử lý

1. Phân loại nguồn bằng quy tắc. Tiêu đề đơn thuần, ảnh chưa đọc được, PDF scan, đề trống vào hàng cần nguồn trước khi gọi AI.
2. Đọc hợp đồng chấm từ `ProblemData` và metadata công khai trong `init.yml`: loại bài, I/O file, checker. Không đưa test ẩn, nội dung checker/generator hoặc tài khoản vào prompt. DB/init/đề mâu thuẫn thì dừng để sửa dữ liệu; bài tương tác/hàm/output-only cần luồng chấm chuyên biệt.
3. Tạo diff cấu hình cơ học theo quy tắc hiện có. Chuẩn hoá đề khi chưa đạt format nghiêm ngặt; chỉ sửa heading nếu có thể làm bằng quy tắc.
4. Tìm code tác giả, chấm lại dưới bộ test hiện hành. Code tác giả dùng macro/ghép chuỗi tên file vẫn được chấm thật; tiền kiểm chuỗi literal chỉ là chốt bảo thủ cho code sinh mới, không loại code tác giả. Hiển thị rõ không tìm được, chưa kiểm chứng đạt, hoặc đã đạt. Solution văn bản hiện có là nguồn tham khảo riêng.
5. Một lượt Luna trả lời giải, code, 1–4 tags và độ khó ước lượng kèm khoảng điểm, lý do và độ tin cậy. Rating chính thức Codeforces chỉ dùng khi người thao tác đã xác nhận nguồn và backend lấy được dữ liệu.
6. Với code C++ **sinh mới**, có thể nối file I/O bằng adapter nhỏ trước `main` nếu không có file handling hoặc cú pháp mơ hồ. Không đổi thuật toán, không sửa code tác giả, không đổi cấu hình bài. Code sau adapter vẫn phải đạt đủ test thực tế.
7. Kiểm chứng code; sai thuật toán/biên/giới hạn mới sửa một lượt Luna rồi nâng Sol nếu được phép. Hạ tầng, metadata, nguồn thiếu không nâng model. Phản hồi sửa có hợp đồng I/O và hướng kiểm tra cụ thể; không gửi hidden test vào prompt.
8. Policy xét từng nhóm trường. Code đạt nhưng đề/tags/rating chưa đủ bằng chứng hiển thị “Code đạt đủ điểm · còn phần cần kiểm tra”.

## Prompt và kiểm tra đầu ra

- Giữ nguyên câu, công thức, giới hạn và ví dụ khi có thể; ưu tiên thay đổi định dạng tối thiểu.
- Dữ liệu nguồn là dữ liệu, không phải chỉ dẫn được phép làm thay đổi nhiệm vụ.
- Ví dụ chỉ nằm ở `examples`; không nhét vào `output`. Server dựng Markdown, hiển thị đúng tên file và cấp heading `### Input/Output/Example`.
- Đối chiếu ví dụ ở cả nguồn, JSON cấu trúc và Markdown hiển thị. Hỗ trợ fence không ghi ngôn ngữ, `sample`, `text`, HTML `<pre>`. Không suy ra “không có ví dụ” chỉ vì không tìm được đúng một cú pháp.
- Chặn ví dụ thay đổi/mất, tên file mất/thay đổi, ảnh mất, ký tự điều khiển và nội dung nằm sai trường. Ví dụ ở bảng phức tạp hoặc plain text chưa trích được vẫn chưa có bằng chứng tự áp dụng.
- Phân biệt lỗi cosmetic/Scoring với mơ hồ làm thay đổi đáp án. Không tự sửa tổng phần trăm subtask bằng suy đoán.
- Code chỉ trả một lần, không lặp lại trong các trường diễn giải. Lời giải phải có thuật toán, tính đúng và độ phức tạp không rỗng.
- Bài dễ: hướng dẫn ngắn, từ ngữ dễ hiểu, ý tưởng trực tiếp, lý do đúng và độ phức tạp ngắn gọn; vẫn có một code mẫu hoàn chỉnh. Không thêm mục nhận xét/trường hợp biên khi không có ích.
- Bài khó: hướng dẫn theo các subtask thực sự trong đề, từ dễ tới khó, trước lời giải đầy đủ. Mỗi subtask phải có cách làm và độ phức tạp, không chỉ chép giới hạn; subtask dễ giải thích ngắn. Code mẫu cuối giải được toàn bộ bài, không lặp code cho từng subtask.
- Nếu đề khó không có subtask, trình bày cách đơn giản rồi tối ưu trong phần thuật toán, không tự gán subtask/điểm. Nếu cùng một thuật toán đơn giản giải mọi subtask, không lặp lại các phần giống nhau. Prompt `std_prompts_v3` ghi nhận quy ước độ dài này; các đề xuất trước vẫn được giữ nguyên.
- Độ khó là ước lượng theo thang Codeforces; không gọi là rating chính thức. Kiểm tra miền 800–3500, điểm chính theo bội 100 và `low <= rating <= high`. Khi không đủ ràng buộc phải đánh dấu thiếu bằng chứng.

## Tự động áp dụng và giảm duyệt

Mặc định `STANDARDIZATION_GUIDED_AUTO_APPLY=False`: guided chỉ tạo bản nháp. Muốn tự áp dụng, đặt `True` trong `local_settings.py` của đúng site cùng lúc triển khai policy đầu tiên. **Vẫn cần một `StandardizationPolicy` được quản trị viên bật và đủ tất cả điều kiện của policy.** Policy guided bắt buộc chỉ rõ `prompt_versions_json`, không chấp nhận phạm vi phiên bản trống; phiên bản không có trong danh sách (kể cả các nháp cũ) bị giữ lại.

| Nhóm | Điều kiện chính để tự áp dụng |
|---|---|
| Cấu hình | Quy tắc cơ học giải được, không trong kỳ thi đang chạy, nguồn không đổi ngoài chuỗi cập nhật đã kiểm soát |
| Đề AI xử lý | Format nghiêm ngặt, không cảnh báo/chỗ chưa xác minh; code tự đối chiếu bảo toàn token nội dung, ví dụ, tên file và ảnh từ snapshot — không tin nhãn “matched” do model/record tự khai |
| Lời giải | Code đúng phiên bản đạt đủ test; diễn giải đủ các phần, không còn mơ hồ |
| Tags | Tag thuộc danh mục, không còn tag chưa ánh xạ, đạt ngưỡng tin cậy và có lời giải liên kết đã kiểm chứng hiện hành |
| Points ước lượng | Khoảng điểm hợp lệ, đủ lý do, đạt ngưỡng tin cậy, lời giải liên kết đã kiểm chứng và đạt điều kiện hiệu chuẩn của policy |

Chứng minh bảo toàn đề được thiết kế thận trọng: không tự chứng nhận một bản viết lại ngữ nghĩa, bảng phức tạp hoặc source code trong đề chỉ bằng việc số liệu trông giống nhau. Các trường hợp này được giữ lại. Vì vậy nên giữ prompt ở chế độ sửa định dạng tối thiểu; sửa câu chưa rõ là ngoại lệ cần bằng chứng mạnh hơn.

Hiệu chuẩn rating ước lượng mặc định cần 30 quyết định duyệt với tỷ lệ chấp nhận tối thiểu 80%, tính theo **nhóm + phiên bản prompt + bước + model**. Đây là giai đoạn hiệu chuẩn mẫu ban đầu, không phải duyệt mọi bài. Không tự bỏ điều kiện bằng cách dùng điểm tự tin của model. Rating chính thức từ nguồn Codeforces đã xác nhận không cần hiệu chuẩn ước lượng AI.

Policy giữ daily/total cap, hậu kiểm mẫu, tự dừng khi tỷ lệ sai vượt ngưỡng và khả năng hoàn tác. Các worker khoá policy và bài khi áp dụng để tránh dùng bộ đếm cũ hoặc vượt cap do hai lượt cùng đọc một giá trị.

Sau một lần rejudge thất bại, policy không chấm lại cùng code và hash cấu hình/test ở các đợt quét tiếp theo, kể cả khi xét các tags/Points liên kết. Proposal vẫn bị giữ; người dùng có thể chủ động Verify. Đổi code hoặc cấu hình/test thì mới xét lượt chấm mới. Với lỗi hạ tầng `UNKNOWN`, chờ ít nhất 300 giây và tối đa ba kết quả UNKNOWN cho cùng code/cấu hình; lỗi nguồn/metadata không tự thử lại. Kết quả tiền kiểm và lỗi hạ tầng cũng lưu hash code/test để giới hạn này có hiệu lực.

Batch đã hoàn thành vẫn được xét áp dụng; batch dừng/tạm dừng không được xét. Các trường cập nhật tự động trong cùng snapshot có hash tiếp nối được lưu để không làm các trường còn lại bị đánh dấu cũ nhầm. Chỉnh sửa ngoài chuỗi hoặc test thay đổi vẫn chặn. Cấu hình được xét trước; nếu làm kết quả chấm cũ mất hiệu lực, policy có thể chấm lại code đã lưu ngoài transaction, không gọi lại AI. Số lần thử lại khi máy chấm chưa xác định được giới hạn.

Nếu chuẩn hoá nội dung làm hash đề thay đổi ngoài phép sửa heading, lời giải cũ có thể phải được kiểm tra lại về nguồn; không mặc định miễn kiểm chứng để tăng số bài tự áp dụng.

## Chạy theo lô

Các lệnh sau dành cho môi trường triển khai có DB, worker và máy chấm. `--actor` là tài khoản có quyền duyệt chuẩn hoá và xem nguồn các bài được chọn.

```sh
# Xem trước, không tạo batch, không gọi AI.
python manage.py standardize_guided --actor MrTee --from-id 1 --through-id 10000 --limit 100

# Đưa tối đa 100 bài trong khoảng vào hàng đợi; bao gồm đề và cấu hình.
python manage.py standardize_guided --actor MrTee --from-id 1 --through-id 10000 --limit 100 --budget-per-problem 0.10 --enqueue

# Lô chẩn đoán cụ thể, không nâng Sol.
python manage.py standardize_guided --actor MrTee --codes pfsqpairs,pvhoi4i,lqdoj2022r6dyslexia --no-sol --enqueue
```

Lệnh trả JSONL gồm ID, mã, batch và kết quả. Bài đang có lượt hoạt động được giữ nguyên, không tạo lượt trùng. Lấy ID cuối đã xử lý làm mốc `--from-id` tiếp theo. `--codes`/`--ids` cũng phải nằm trong `--limit`; không gửi hàng nghìn bài vào một request web kéo dài. Nếu worker dispatch lỗi, batch được đánh dấu lỗi để có thể chạy lại.

Không chạy lại cả lô khi chỉ cần verify: thao tác **Verify** trên proposal dùng code đã lưu, không gọi AI. Timeout mới lưu submission ID và hash nguồn/test; nếu submission đó đã xong, Verify đọc lại chính kết quả đó thay vì nộp thêm một lần. Timeout cũ không lưu ID không thể tự khôi phục liên kết bằng suy đoán.

## Ví dụ policy ban đầu

```sh
# Tạo policy giới hạn 500 bài đầu và 100 đề xuất/ngày, có hậu kiểm 10%.
python manage.py standardize_policy create --policy guided_v2_pilot --name "Guided v2 pilot" --groups config,solution,tags,content --prompt-versions std_prompts_v3,guided_solution_v1 --from-id 1 --through-id 500 --daily-cap 100 --total-cap 500 --sample-rate 0.1 --include-public

# Xem điều kiện trước, rồi bật policy khi phạm vi phù hợp.
python manage.py standardize_policy evaluate --policy guided_v2_pilot --proposal-id 123
python manage.py standardize_policy enable --policy guided_v2_pilot
python manage.py standardize_policy sweep --policy guided_v2_pilot --limit 100
```

`guided_solution_v1` ở danh sách phiên bản là proposal cấu hình/heading theo quy tắc; proposal AI mới dùng `std_prompts_v3`. Cap của policy tính **đề xuất**, không phải bài; một bài có thể có nhiều nhóm. Beat hiện có quét policy mỗi 10 phút. Rating ước lượng có thể dùng policy riêng sau hiệu chuẩn để không chặn các nhóm khác.

## Chi phí và lựa chọn model

- Giữ mặc định Luna và Sol hiện hành, chưa đổi model dựa vào ba WA do I/O của demo. Model chính/phụ vẫn cấu hình được bằng `STANDARDIZATION_GUIDED_MODEL` và `STANDARDIZATION_GUIDED_FALLBACK_MODEL`.
- Sinh lời giải, tags và rating chung một request; không gọi model riêng để đọc lại cùng bài cho mỗi trường.
- Bản nháp được tái sử dụng theo nguồn, phiên bản prompt và model. Dừng khi nguồn/test thay đổi thay vì áp dụng bản cũ.
- Chỉ bỏ qua toàn bộ lượt AI khi đã có Solution đủ phần, tags và rating đã áp dụng cho đúng phiên bản đề; một số Points cũ có thể là điểm chấm, không được mặc định coi là độ khó.
- Giới hạn số lần sửa/nâng cấp và ngân sách theo bài/lô/ngày. Trần API hiện dựa trên chi phí **đã ghi nhận**, không phải đặt chỗ cho request đang chạy; vẫn có thể vượt phần tiền còn lại ở một request. Không coi `--budget-per-problem` là bảo đảm số tiền tối đa tuyệt đối.
- Theo dõi chi phí cho một bài đạt các tiêu chí, tỷ lệ giữ lại và hiệu quả cứu bài của tầng nâng cấp. Prompt caching đã có ở provider nhưng phải đo cache-hit thực tế; không giả định cache chia sẻ giữa các model.

## Kiểm thử và triển khai

1. Chạy toàn bộ `judge/tests/test_standardization*.py` trên DB kiểm thử riêng; thêm bài hồi quy cho lỗi I/O demo, mất ví dụ, cảnh báo, nguồn thiếu, rating và timeout.
2. Chạy lại code demo trên dữ liệu hiện có bằng adapter thật. Kết quả test nhỏ/tập lưu trước không thay thế full verdict production.
3. Không bật policy rộng ngay. Chạy lô giới hạn, kiểm tra diff và hậu kiểm mẫu; mở rộng khi số liệu thực tế đạt mục tiêu.
4. Triển khai cả web và worker, khởi động lại worker để dùng prompt mới. Chỉ bật `STANDARDIZATION_GUIDED_AUTO_APPLY=True` riêng site đã chuẩn bị policy, phiên bản prompt và phạm vi phù hợp. Site còn lại giữ mặc định False.
5. Không đưa khoá API, file test riêng tư, tài khoản QA hoặc dữ liệu audit local vào Git.
