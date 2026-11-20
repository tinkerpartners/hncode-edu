# Thiết kế chuẩn hoá tự động, giảm tối đa việc duyệt từng bài

**Ngày nghiên cứu:** 20/11/2026. **Phạm vi:** code hiện tại và 150 bài thử đã có; không gọi AI mới, không apply dữ liệu hoặc bật policy production.

**Quyết định của chủ hệ thống:** bài thiếu căn cứ hoặc thiếu dữ liệu phải được bỏ qua và để riêng để xử lý sau. Không tạo hàng nghìn yêu cầu duyệt tay cho những trường hợp này.

## 1. Kết luận đề xuất

Chuyển từ “AI viết lại mọi bài rồi người dùng duyệt” sang:

> **Giữ phần đã đúng → chuẩn hoá bằng quy tắc → AI chỉ làm phần còn thiếu → máy tự kiểm chứng → tự áp dụng từng phần đủ căn cứ → tách riêng phần chưa xác minh được.**

- Không yêu cầu duyệt trước từng bài trong nhóm đã đạt điều kiện tự động.
- Không dùng một trường chưa rõ để chặn mọi phần khác của một bài đủ nguồn.
- Không cố hoàn thành bài thiếu định nghĩa, thiếu test hoặc mâu thuẫn giao diện chấm. Dừng trước khi tốn AI khi có thể.
- Không dùng “hai model đồng ý” hay “model tự tin cao” thay cho bằng chứng.
- Không phải viết lại văn phong của mọi đề. Mục tiêu là nội dung rõ, đúng và bố cục chuẩn; giữ nguyên câu chữ rõ ràng giúp giảm cả lỗi lẫn nhu cầu duyệt.
- Hậu kiểm chỉ xem một mẫu có giới hạn, không biến toàn bộ bản nháp thành công việc bắt buộc của admin.

Các gate và phân luồng đầu tiên đã được triển khai; xem [phạm vi thực tế và giới hạn](STANDARDIZATION_LOW_REVIEW_IMPLEMENTATION.md). Tài liệu thiết kế này vẫn chứa các bước tiếp theo, không phải tuyên bố mọi cơ chế đã hoàn thành.

## 2. Những gì dữ liệu 150 bài cho thấy

Phân tích chỉ đọc DB QA, gồm 50 bài cũ và 100 bài mới, không trùng mã nguồn. Không chạy model hoặc cập nhật proposal trong nghiên cứu này.

| Kết quả quan sát | Số lượng | Cách hiểu đúng |
| --- | ---: | --- |
| Code đạt trong các lượt gốc | 112/150 | Đạt bộ test, chưa chứng nhận toàn bộ đề/lời giải/tag/rating |
| Có thêm hai bản sửa kiểm chứng riêng | 2 | `flags` và `bangdientunammoi`; giữ nguyên số liệu thất bại gốc |
| Có ít nhất một bản code đã xác minh sau bổ sung | 114/150 | Nền tảng để xử lý tiếp, không phải 114 bài đã chuẩn hoá hoàn chỉnh |
| Cần tách riêng về nguồn, hợp đồng hoặc chế độ chấm | 36/150 | Không đưa vào hàng chờ duyệt thường xuyên |
| Trong 114 bài, sửa heading được bằng quy tắc hiện có | 41 | Ứng viên sửa heading, không đồng nghĩa đầy đủ mọi bố cục ví dụ |
| Bản viết lại AI vượt được chứng minh bảo toàn nội dung hiện tại, trong 114 bài | 0 | Gate bảo toàn rất chặt; không kết luận tất cả bản AI đều sai |
| Trong 114 bài, không trích được cặp ví dụ từ nguồn bằng parser hiện tại | 21 | Có thể là không có ví dụ hoặc parser chưa hỗ trợ; không đồng nhất với thiếu đề |
| Tag tự báo độ tin cậy cao, trong 114 bài | 112 | “High confidence” gần như không phân tách được rủi ro |

36 bài tách riêng: 16 thiếu/chưa đọc được nguồn; 10 chưa rõ ngữ nghĩa/quy ước; 6 mâu thuẫn hợp đồng; 4 cần luồng chấm đặc biệt. Các con số là phân loại vận hành trên tập đã thử, không phải tỷ lệ đại diện cho toàn ngân hàng.

**Hệ quả:** chỉ bật policy hiện tại hoặc hạ ngưỡng confidence không giải quyết được việc duyệt. Cần thay đổi cách tạo đề chuẩn và cách thu thập bằng chứng. Cũng không được gọi 36 bài tách riêng là “36 bài cần anh duyệt ngay”.

### Mô phỏng chỉ đọc policy hiện có

Dùng một policy **chưa lưu**, bật cờ guided chỉ trong tiến trình mô phỏng, cho phép `HEADINGS_OK` để quan sát các đề xuất heading và không gọi apply:

- Có hai trường hợp bộ chọn candidate lấy solution pending cũ phía sau solution mới đang `needs_clarification`. Các gate khác hiện vẫn có thể chặn chúng; chưa có bằng chứng các bản cũ này đã bị áp dụng sai.
- 177 đề xuất QA bị gate apply chặn vì batch cap bằng 0 vốn được đặt để không gọi AI. Điều này xác nhận sự ghép nhầm giữa ngân sách sinh AI và bước apply, không có nghĩa 177 bài production đang lỗi.
- Có 9 đề xuất cấu hình và 1 đề xuất heading của các bài nên tách riêng vẫn qua điều kiện của profile mô phỏng. Do đó phải bổ sung gate toàn bài trước các gate từng trường để tuân thủ yêu cầu bỏ qua bài thiếu căn cứ.
- Các số “qua gate” của profile mô phỏng không được coi là bằng chứng đủ điều kiện tự áp dụng production. Không policy/decision nào được tạo và không dữ liệu bài nào bị sửa.

## 3. Bốn kết quả vận hành, thay cho một hàng chờ duyệt chung

| Kết quả | Hệ thống làm gì | Việc của admin |
| --- | --- | --- |
| `AUTO_DONE` | Đã áp dụng các phần cần chuẩn và đủ bằng chứng; phần đã đúng giữ nguyên | Chỉ hậu kiểm nếu được lấy mẫu |
| `AUTO_PARTIAL` | Một số phần tự hoàn tất, phần còn lại giữ nguyên và ghi lý do | Không bắt buộc mở bài để duyệt phần đang treo |
| `DEFERRED_SOURCE` | Thiếu/không rõ nguồn, test hoặc hợp đồng; bỏ qua trước AI | Để sau, chỉ xuất hiện ở kho tách riêng |
| `DEFERRED_VERIFICATION` | Nguồn có nhưng chưa vượt kiểm chứng trong giới hạn thử/ngân sách | Để sau; không kéo vào hàng duyệt mặc định |

`RETRY_INFRA` là trạng thái kỹ thuật tạm thời khi mất kết nối/queue timeout, không phải bài sai và không cần người duyệt nội dung.

“Bỏ qua” chỉ áp dụng cho quy trình chuẩn hoá. Không xoá bài, không ẩn bài khỏi web và không thay đổi dữ liệu gốc chỉ vì nó đang bị tách riêng.

### Dừng cả bài hay chỉ dừng một trường?

- **Dừng cả bài:** không biết yêu cầu toán học, thiếu test để xác minh tác vụ cần chấm, file chấm thiếu/hỏng, hợp đồng mâu thuẫn, PDF chưa đọc được, giao diện đặc biệt chưa được hỗ trợ. Không cập nhật các nhóm khác của bài đó trong batch này.
- **Chỉ dừng một trường:** đề và bộ test đầy đủ nhưng rating còn rộng; một tag chưa ánh xạ được; bản viết lại đề chưa đủ bằng chứng bảo toàn. Các phần khác vẫn được xem xét riêng.
- Thiếu code AC không tự động là thiếu căn cứ: nếu đề và test đủ, AI vẫn có thể tự giải rồi kiểm chứng.
- Thiếu ví dụ không tự động là thiếu đề: nếu yêu cầu, input/output, giới hạn và bộ test đủ thì giữ trạng thái “đề gốc chưa có ví dụ”, không tự chế ví dụ và không buộc admin duyệt vì lý do này.

## 4. Cửa kiểm tra nguồn dùng chung, chạy trước mọi request AI

Tạo `source_readiness()` duy nhất để UI, CLI, pipeline và policy cùng dùng. Hiện `classify.py`, `guided.py` và `steps.py` có các kiểm tra khác nhau; phân tích có một bài được classifier coi là có chữ nhưng guided đã xác định thiếu đề.

Kiểm tra:

1. Có yêu cầu đủ xác định đáp án và nguồn đọc được; không chỉ có tên bài hoặc mẫu input/output.
2. Định dạng, giới hạn và quy tắc cần thiết cho cách giải không mâu thuẫn.
3. Hợp đồng thật từ DB/init.yml thống nhất với đề; đúng chữ hoa/thường của file IO.
4. Test/checker/generator cần thiết tồn tại, đọc được, cấu hình hợp lệ; bộ test thực sự được chấm đầy đủ, không chỉ pretest.
5. Loại bài được luồng hiện tại hỗ trợ.

Số lượng test nhiều không chứng minh chất lượng. Một test lớn cũng không mặc nhiên là test kém. Cần kiểm tra tính hợp lệ và phạm vi kiểm chứng, không đặt một ngưỡng số test để thay thế đánh giá này.

Khi tách riêng, lưu `reason_code`, phần dữ liệu thiếu, `source_hash`, `judge_hash`, thời điểm và hành động bổ sung cần làm. Chỉ tự xét lại khi fingerprint liên quan thay đổi; không gọi lại AI theo lịch nếu nguồn vẫn y nguyên.

Ví dụ: PDF hỏng → thay file mới mới xét lại; mâu thuẫn file IO → sửa metadata/đề mới xét lại; hết ngân sách → được cấp ngân sách mới mới tiếp tục; lỗi mạng → retry có giới hạn, không bắt sửa đề.

## 5. Chuẩn hoá đề theo hướng bảo toàn, không viết lại tự do

### 5.1. Mặc định giữ câu chữ, đổi cách trình bày bằng parser/renderer

Parser tạo cây các khối: mô tả, yêu cầu, input, output, giới hạn/subtask, ví dụ, chú thích, ảnh và bảng. Mỗi khối gắn vị trí và hash nguồn. Renderer tạo đúng `### Input`, `### Output`, `### Example`, và `### Scoring` khi nguồn có.

- Với heading đã nhận diện chắc chắn: sửa bằng quy tắc, không gọi model.
- Đoạn văn rõ: chuyển nguyên văn, không tự viết lại cho “hay hơn”.
- Ví dụ: lấy dữ liệu trực tiếp từ nguồn và gắn ghi chú đúng ví dụ; AI không nhập lại số liệu.
- Ảnh và bảng: giữ tham chiếu/nội dung và thứ tự. Bảng HTML phức tạp có thể giữ HTML trong đề thay vì ép AI chép lại.
- Chỉ dùng mapping heading xác định, không thay các từ như “Kết quả” trong mọi câu bằng một section.
- Không biến `105` thành `10^5`, `<` thành `<=`, thay min/max, file IO hoặc phần trăm subtask bằng suy đoán.

Bằng chứng tự áp dụng là **các khối nội dung được giữ, thứ tự trong từng khối không đổi, biến/số/công thức/ví dụ/tài nguyên không đổi**. Không dùng so sánh tập từ hoặc chỉ số giống văn bản để chứng nhận ý nghĩa.

### 5.2. Khi bố cục không thể phân đoạn chắc chắn

AI chỉ đề xuất vị trí/loại section và trích đoạn nguồn tương ứng; máy chủ kiểm tra mọi trích đoạn có thật, không bị thiếu/lặp/đổi nội dung, rồi tự render. Nếu không chứng minh được, thử một bước sửa có giới hạn. Không được thì giữ nguyên trường đề và ghi `DEFERRED_VERIFICATION` cho trường này.

Việc viết lại nội dung toán học thật sự là chế độ riêng có kiểm chứng ngữ nghĩa. Không buộc mọi bài đi qua chế độ này chỉ để đạt định dạng HNCode.

### 5.3. Tách cảnh báo có căn cứ

| Nhóm cảnh báo | Xử lý |
| --- | --- |
| Trình bày đã được sửa bằng quy tắc có chứng minh | Không bắt duyệt |
| Nguồn không có ví dụ nhưng bài đủ định nghĩa/test | Giữ ghi nhận, tiếp tục các phần hợp lệ |
| AI trả sai schema/nhét ví dụ vào body | Máy chủ kiểm tra, sửa bằng quy tắc khi chứng minh được hoặc cho một lượt sửa JSON |
| Scoring ghi sai tổng nhưng giữ nguyên nguồn, không ảnh hưởng lời giải | Không ép bịa lại phần trăm; tách cảnh báo khỏi gate thuật toán |
| Ví dụ mâu thuẫn đáp án, mất giới hạn quan trọng, thay quy tắc toán | Tách cả bài, không tự cho qua |

Không phân mức bằng một regex xoá mọi câu có chữ “warning”. Mỗi cảnh báo phải có mã loại, vị trí nguồn, điều kiện đã kiểm tra và phạm vi ảnh hưởng. Chưa phân loại được thì giữ phần liên quan.

## 6. Lời giải và code: khai thác căn cứ sẵn có trước khi sinh mới

Thứ tự ưu tiên:

1. Giữ Solution đã có chứng nhận nội dung và code hiện hành nếu còn khớp nguồn; không thay nó chỉ vì muốn có lời giải AI mới.
2. Dùng code tác giả lưu hoặc code AC mà tài khoản dịch vụ được phép đọc. Chấm lại trên bộ test hiện tại; nhãn AC lịch sử không đủ.
3. Không có reference đủ điều kiện thì Luna tự giải.
4. CE/WA/TLE... → phản hồi lỗi có ích → một lượt sửa Luna; còn sai hoặc cần suy luận sâu thì Sol.
5. Hết số lượt/ngân sách hoặc kiểm chứng còn mâu thuẫn → `DEFERRED_VERIFICATION`, không chuyển sang yêu cầu người dùng đọc mọi bản thử.

Quyền đọc code không được mở rộng chỉ để tăng tỷ lệ tự động. Giữ cơ chế kiểm quyền và cờ cho phép dùng bài nộp tham chiếu; không lấy ngẫu nhiên mọi code học sinh trong DB. Không gửi thông tin tài khoản hay hidden test vào model.

### Bằng chứng cần tách thành các chiều

- Nguồn toán học đủ rõ.
- Hợp đồng chấm thống nhất và còn đúng phiên bản.
- Code compile và đạt đầy đủ test hiện tại.
- Các ví dụ công khai và ca biên độc lập được kiểm tra.
- Diễn giải, công thức, độ phức tạp và code không mâu thuẫn.
- Không có phản ví dụ đang mở.

Không rút tất cả thành một điểm `confidence=0.98`. AI tự nhận độ tin cậy cao không phải xác suất sai đo được.

### Kiểm chứng tăng cường để ít phải duyệt lời giải

- Có reference đã chấm: so sánh ứng viên với reference trên ca nhỏ/ngẫu nhiên hợp lệ; không xem hai bản sao code là hai lời giải độc lập.
- Bài phù hợp: tạo brute-force trực tiếp từ định nghĩa cho kích thước nhỏ, kiểm tra oracle trên ví dụ trước, rồi so với code tối ưu. Generator phải tuân thủ ràng buộc thật.
- Test biên: overflow, rỗng/nhỏ nhất nếu hợp lệ, trùng nhau, âm/0, đồ thị rời nếu được phép, tie-break, nghiệm không tồn tại, khép vòng, giới hạn thời gian.
- Nhiều đáp án: dùng checker đúng và kiểm tra tính hợp lệ, không so output với một đáp án duy nhất.
- Reviewer kiểm tra độc lập trả về các mâu thuẫn cụ thể, vị trí code/công thức và phản ví dụ có thể chạy. Không lấy “looks correct” làm chứng nhận.
- Bài khó có subtask thật: hướng dẫn tiến triển, subtask dễ ngắn; bài dễ ngắn gọn, vẫn có code mẫu. Kiểm tra code cuối thực sự giải giới hạn đầy đủ.

Brute-force và agreement cũng có thể cùng sai. Chúng là bằng chứng bổ sung, không phải chứng minh tổng quát. Dùng sandbox, hạn mức CPU/RAM/process, tắt mạng cho chương trình sinh/chấm thử. Không tự thêm test AI vào bộ test chính thức hoặc đổi checker.

Nếu chưa đủ bằng chứng để áp dụng diễn giải thì giữ trường đó; code AC không buộc cả bài phải đợi rating hoặc một cảnh báo trình bày.

## 7. Tag và Points: giảm duyệt bằng dữ liệu chuẩn, không bằng hạ ngưỡng

### Tag

- Chuẩn hoá catalogue/alias một lần, giữ định danh ổn định.
- Chỉ gán 1–4 tag có vai trò trong lời giải thực tế; không thêm mọi kỹ thuật xuất hiện trong thư viện hoặc code không dùng.
- Mỗi tag có căn cứ: bước thuật toán/cấu trúc dữ liệu/công thức nào sử dụng nó. Kiểm tra khác với việc model lặp lại tên tag.
- Giai đoạn đầu chỉ tự bổ sung tag đã xác minh, xoá “Chưa phân loại” khi có tag thật. Không tự xoá tag do người quản trị gắn nếu chưa có căn cứ thay thế; cách `_apply_tags()` hiện thay toàn bộ tập tag cần được tách thành add/remove có provenance.
- Nếu có tag chưa rõ, giữ riêng tag đó; không làm chậm đề/code đã đủ điều kiện.

### Points theo thang Codeforces

- Nguồn Codeforces thật và đối chiếu đúng bài/giới hạn → dùng rating nguồn được ghi phiên bản. Không suy ra chỉ từ mã bài trông giống Codeforces.
- Bài nội bộ → so với bộ bài mốc đã có rating được xác nhận, xét cách giải đơn giản nhất phù hợp giới hạn, số quan sát cần thiết và độ khó cài đặt.
- Rater và kiểm tra chéo xuất khoảng điểm/lập luận. Khoảng rộng hoặc bất đồng đáng kể → giữ Points hiện tại và tách trường rating, không đẩy admin duyệt cả bài.
- Ngưỡng thử nghiệm có thể là hai ước lượng lệch không quá 200 và khoảng cuối rộng không quá 200, nhưng chỉ sau hiệu chuẩn; đây không phải bảo đảm độ chính xác và không được xem agreement là bằng chứng độc lập duy nhất.
- Phân biệt Points cũ là điểm hoàn thành với rating độ khó. Không dùng chênh lệch từ 100 lên 1200 để tự kết luận bất thường nếu hai giá trị khác ý nghĩa.
- Khi cập nhật Points/Partial, gom tác vụ đồng bộ điểm dẫn xuất theo trạng thái cuối của bài, tránh tính lại nhiều lần vì các nhóm được áp dụng liên tiếp.

### Hiệu chuẩn không buộc duyệt lặp lại mọi bài

Ưu tiên bộ mốc có nhãn đáng tin sẵn có; người quản trị chỉ xác nhận các mẫu đại diện còn thiếu **một lần cho một phạm vi**, không xác nhận từng đề xuất. Tách bộ mốc dùng tham chiếu khỏi tập kiểm tra để không tự đánh giá trên chính câu trả lời đã đưa cho model.

Đo tag bằng đúng/sai theo nhãn, rating bằng sai số và tỷ lệ lệch trong khoảng mục tiêu. “Approve proposal” không mặc nhiên là nhãn rating chuẩn. Một proposal không được tính thành nhiều mẫu độc lập do có nhiều decision.

Thay đổi prompt chỉ về trình bày không nhất thiết xoá mọi hiệu chuẩn rating; thay model, quy tắc chấm hoặc thang rating thì phải kiểm tra tác động. Không chuyển hiệu chuẩn giữa các phạm vi không tương đương.

## 8. Cấu hình cơ học theo quy tắc của chủ hệ thống

Tự xử lý với resolver chắc chắn: thêm `MrTee`, Solution riêng tư, Partial bật, bổ sung CPP17/CPP20/PY3/PYPY3 khi tương thích, RAM ít nhất 1024 MB và giữ giá trị cao hơn đã có.

Không áp dụng profile chương trình thường cho bài hàm/tương tác/output-only chưa hỗ trợ. Không tự đổi thời gian, checker, file IO, test hoặc xóa ngôn ngữ đã có ngoài phạm vi yêu cầu.

Tính cấu hình đích trước khi chấm để tránh chấm xong rồi tự đổi môi trường và phải gọi AI lại. Kiểm chứng target config được gắn với fingerprint; thay đổi giữa chừng thì bằng chứng cũ không dùng được. Không giữ khóa DB trong lúc gọi API hoặc chờ judge.

## 9. Tối ưu chi phí và chống lặp

- Một context nguồn đã phân tích, dùng lại facts có vị trí trích dẫn, source hash và các reference được phép; reviewer vẫn được đọc phần nguồn cần thiết, không chỉ tin summary AI.
- Dùng hash theo thành phần: nội dung/định dạng, test+config, code, editorial, catalogue tag, bộ mốc rating. Đổi author hay Points không nên làm sinh lại thuật toán không đổi.
- Kiểm tra cache/dedup trước khi gọi model; dedup của lượt sửa phải phân biệt diagnostic/phản ví dụ mới nhưng vẫn bị giới hạn tổng số lượt.
- Luna chính, Sol theo tín hiệu kiểm chứng khó: phản ví dụ chưa sửa, bất biến chưa chứng minh, complexity sai giới hạn, bất đồng giữa reference và code. Không dựa riêng vào rating do Luna tự dự đoán.
- Dừng riêng các bài thiếu nguồn ngay; không cho Sol đọc lại dữ liệu vẫn thiếu.
- Đưa cơ chế giữ trước chi phí theo request của benchmark vào production trước khi tăng worker; trần hiện có chỉ kiểm trước request có thể vượt khi nhiều request đang chạy.
- AI budget giới hạn request AI; không dùng nó để chặn việc áp dụng một kết quả đã xác minh mà bước apply không gọi AI.

Chi phí 0,390008 USD của đợt 100 + kiểm tra bổ sung là số liệu đã đo, không dùng làm cam kết chi phí của pipeline có reviewer/oracle mới. Phải đo thêm chi phí trên mỗi bài thực sự tự hoàn tất và tỷ lệ tách riêng.

## 10. Hậu kiểm ít nhưng có khả năng phát hiện lỗi

- Sau khi một phạm vi đã có bằng chứng/hiệu chuẩn, không bắt duyệt trước từng bài; tự áp dụng và chọn mẫu hậu kiểm.
- Mẫu gồm phần ngẫu nhiên và phần tập trung vào nguồn/thuật toán/checker/model mới. Không chỉ kiểm những bài dễ hoặc được model tự tin cao.
- Tỷ lệ ban đầu gợi ý 5%, tối thiểu một số mẫu theo đợt lớn và có trần lượng việc; chỉ giảm sau dữ liệu thực tế. Đây là tham số vận hành, không phải chứng minh còn tối đa 5% sai hay 95% đúng.
- Lỗi nghiêm trọng đầu tiên đã xác nhận: dừng ngay phạm vi policy liên quan; không đợi đủ 10 mẫu rồi mới xử lý. Các lỗi trình bày nhỏ có mức riêng.
- Nếu mẫu chờ hậu kiểm tăng quá giới hạn, giảm/tạm dừng rollout phạm vi mới; không tiếp tục mở rộng vô hạn rồi để người dùng gánh backlog.
- Lưu snapshot/decision cho mỗi lần apply. Khôi phục chỉ khi trạng thái hiện tại còn khớp kết quả đã apply; không ghi đè sửa tay mới hơn.
- Chỉ tự rollback phần có bằng chứng xác nhận sai. Các bài cùng fingerprint rủi ro cần tái kiểm chứng theo lô, không kết luận tất cả đều sai từ một mẫu.

Người dùng chỉ cần xem bảng tổng hợp ngoại lệ và mẫu hậu kiểm có giới hạn. Không yêu cầu họ xử lý hết kho đã tách riêng trước khi những bài khác tiếp tục.

## 11. Các điểm code cần sửa trước khi mở tự động rộng

| Ưu tiên | Vị trí hiện tại | Vấn đề / thay đổi cần làm |
| --- | --- | --- |
| P0 | `classify.py`, `guided.py`, `steps.py` | Gom readiness, parking và lý do; chặn thiếu nguồn/test trước mọi AI; không tự requeue khi fingerprint không đổi |
| P0 | `integrity.py::normalized/source_examples` | `dedent` đang làm mất khoảng trắng đầu nội dung mẫu; chưa thể dùng làm chứng minh giữ nguyên byte cho bài nhạy khoảng trắng. Parser phải chỉ bỏ indent bao ngoài Markdown |
| P0 | `policy.py::candidates` | Đang lọc pending trước khi chọn mới nhất; có thể chọn bản pending cũ phía sau bản mới needs_clarification/rejected. Chọn revision hiện hành theo mọi trạng thái trước, không fallback bản cũ |
| P0 | `policy.py::_cond_problem_unchanged` | Đang kiểm AI budget trong gate apply; tách ngân sách gọi model khỏi apply đã có bằng chứng |
| P0 | `policy.py::check_breaker` | Bổ sung dừng ngay với lỗi nghiêm trọng, quota mẫu chưa kiểm và phạm vi dừng theo nhóm/phiên bản |
| P1 | `format_v1.py`, parser mới | Chuẩn hoá bảo toàn khối, alias heading, ví dụ, ảnh/bảng; giữ nguyên văn thay vì AI viết lại toàn bộ |
| P1 | `steps.py::_finalize_content` | Cảnh báo có mã, phạm vi và bằng chứng; một bước sửa schema có giới hạn; không xoá mù cảnh báo |
| P1 | `verification.py`, evidence model | Tách judge đầy đủ, kiểm tra độc lập, critic, rating; hash đúng phiên bản; phản ví dụ chưa giải quyết phải chặn apply |
| P1 | `guided.py::complete_editorial` | Ba heading có ít chữ chưa chứng minh lời giải đúng; dùng chứng nhận/reference và kiểm tra code–diễn giải |
| P1 | `apply.py::_apply_tags` | Add/remove có provenance, không tự ghi đè tag đã xác nhận; không để một tag treo chặn nhóm khác |
| P1 | `policy.py::_cond_calibrated` | Dùng nhãn chuẩn và sai số có đo; không coi 80% approve + confidence high là chuẩn chung cho cập nhật tự động |
| P1 | `budget.py`, provider orchestration | Giữ trước chi phí và release/reserve lỗi có tính phí; cache context và giới hạn retry toàn bài |
| P2 | Views/templates/admin | Mặc định tự động + kho tách riêng; hậu kiểm theo cụm; bản trước/sau và khôi phục; quyền và trạng thái đồng bộ |

Thứ tự triển khai: **readiness/parking + các gate P0 → renderer bảo toàn → kiểm chứng/editorial/tag/rating → UI và rollout**. Có thể tái sử dụng policy, snapshot, verification, decision, rescore và audit hiện có; không cần viết lại cả hệ thống.

### Tổ chức dữ liệu đề xuất

Thêm disposition theo `(problem, field_group hoặc toàn bài, source_version, judge_version)` và reason code, thay vì dùng `needs_clarification` cho mọi tình huống. Các proposal cũ vẫn giữ làm lịch sử.

Evidence có loại rõ: `full_judge`, `public_samples`, `independent_oracle`, `editorial_consistency`, `statement_preservation`, `rating_calibration`. Không để một bản ghi QA mới che mất thông tin judge đầy đủ chỉ vì nó mới nhất; đồng thời không bỏ qua một failed check mới trên cùng code/hash. Policy phải tổng hợp đủ bằng chứng cần thiết và không còn hard failure đang mở.

Plan apply lưu hành động theo nhóm: `keep`, `auto_apply`, `defer`, lý do, hash nguồn/đích và điều kiện. Áp dụng bằng compare-and-swap trong transaction ngắn, đúng revision, đúng quyền; không ghi đè can thiệp của quản trị viên.

## 12. Giao diện và tiêu chí nghiệm thu

Trang chính có: đã quét, tự hoàn tất, tự cập nhật một phần, đang xác minh, tách riêng, mẫu hậu kiểm. Mỗi con số có định nghĩa; không dùng “code AC” làm số “bài chuẩn hoá xong”.

Kho tách riêng lọc theo: thiếu đề, PDF/ảnh chưa đọc, thiếu test, hợp đồng mâu thuẫn, dạng chưa hỗ trợ, chưa vượt kiểm chứng. Có xuất danh sách, gom lý do và nút “xét lại khi đã bổ sung”; không hiện thành popup bắt người dùng xử lý từng bài.

Nghiệm thu tối thiểu:

1. Bài thiếu nguồn/test bị tách trước AI, không thay đổi bài gốc, không sinh vòng lặp request.
2. Bài không có ví dụ nhưng đủ định nghĩa/test không tự bị coi là thiếu đề; không bịa ví dụ.
3. Renderer giữ nội dung quan trọng, số, toán tử, thứ tự, mẫu nhạy khoảng trắng, ảnh, bảng; unsupported layout không được chứng nhận nhầm.
4. AI/critic cùng đồng ý nhưng có phản ví dụ chạy được → không apply.
5. Bản mới bị từ chối/đang treo → không tự dùng bản pending cũ để vượt quyết định mới.
6. Một phần đủ căn cứ có thể hoàn tất trong khi rating/tag khác giữ nguyên; bài thiếu nền tảng vẫn được dừng cả bài.
7. Hết AI budget không chặn apply không gọi AI; nhiều request không vượt trần đã giữ trước.
8. Lỗi nghiêm trọng hậu kiểm dừng đúng phạm vi ngay, khôi phục không đè sửa tay.
9. Người dùng không cần duyệt tất cả các bài bị tách riêng; không có cam kết một tỷ lệ tự động giả định trước khi đo.

Đợt kiểm chứng tiếp theo nên chạy lại offline 150 fixtures cho các gate/renderer, rồi thử auto-apply trên clone local có snapshot. Đo riêng số field tự hoàn tất, lỗi giữ nhầm, lỗi cho qua và lượng hậu kiểm. Chỉ sau đó mới mở policy production theo phạm vi đã kiểm chứng.

## 13. Tài liệu và dữ liệu kiểm tra

- Báo cáo benchmark trước: `docs/STANDARDIZATION_BENCHMARK_100.md` và `docs/STANDARDIZATION_RELIABILITY_V3.md`.
- Nghiên cứu chỉ đọc: `D:\AI-Code\Build_HNCode\.local\openrouter-pilot\outputs\low_review_research_20261120\analysis150.json`.
- Bảng phân luồng và danh sách để riêng được xuất trong `D:\AI-Code\Build_HNCode\outputs\low_review_research_20261120\`.
- Các phép mô phỏng policy chỉ dùng đối tượng chưa lưu và settings cục bộ trong tiến trình, không gọi `sweep`, `auto_apply`, không tạo decision và không bật cấu hình dịch vụ.

**Trạng thái bàn giao:** nghiên cứu/thiết kế và phân tích dữ liệu; chưa triển khai các thay đổi mới nêu ở mục 11, chưa bật tự áp dụng production.
