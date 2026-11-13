# Quy trình gửi code, review, merge và triển khai VPS

## 1. Phương án đề xuất

**Dùng nhánh bàn giao `mrtee-hncode-v1.0.0-rc.2` và mở Draft Pull Request vào `main`.** Nhánh mới kế thừa nhánh cũ `codex/backlog-20261113`, đã tạo ở local. Dev có quyền ghi nhận Git bundle để push vào repo chính; có thể dùng fork nếu phù hợp. Xem [hướng dẫn nhận nhánh và gửi dev](08_GUI_DEV_VA_QUAN_LY_NHANH.md).

Team review và kiểm thử trên nhánh/commit cụ thể, hoàn tất tiêu chí của phạm vi release, rồi maintainer mới merge. Ops triển khai commit đã merge lên staging/VPS theo lịch được thống nhất.

Nhánh đã tồn tại ở `D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113`. Checkout chính đang chạy local có tùy chỉnh bộ cài riêng; dùng checkout review để gửi code.

Tại thời điểm kiểm tra 14/11/2026:

- Tài khoản GitHub đang dùng là `tungfint`, có quyền pull, **không có quyền push** repository tinkerpartners/hncode-edu. Có thể đóng góp qua fork, hoặc maintainer có quyền write nhận nhánh.
- Default branch là main. [Tài liệu deployment của repository](https://github.com/tinkerpartners/hncode-edu/blob/e665d7b/deploy/README.md) mô tả main được bảo vệ và thay đổi thông thường đi qua PR.
- PR [#141](https://github.com/tinkerpartners/hncode-edu/pull/141) và [#142](https://github.com/tinkerpartners/hncode-edu/pull/142) đang mở. Nhánh bàn giao đã tích hợp nội dung hai PR và có sửa tiếp theo.

Gói bàn giao này được chuẩn bị tại local. Việc fork, push, mở PR, merge và triển khai VPS là các bước tiếp theo; chưa thực hiện trong lượt lập tài liệu.

## 2. Quyết định phạm vi trước khi merge

Draft PR ban đầu phù hợp làm nơi review toàn bộ bản tích hợp. Trước khi Ready for review, maintainer chốt một trong các phương án:

| Phương án | Cách thực hiện |
|---|---|
| Review bản tích hợp | Đối chiếu #141/#142, chọn một luồng đưa nội dung vào main; cập nhật nhánh và chạy test sau khi main thay đổi |
| Merge #141/#142 trước | Chờ hai PR merge, rebase nhánh tích hợp lên main mới, giải quyết conflict và chạy lại test; diff PR phải thể hiện phần bổ sung |
| Tách release nhỏ | Dev tách theo nhóm hành vi và phụ thuộc, chạy test từng nhánh; giữ phần AI/kiểm kê chuẩn bị sang PR sau nếu chưa muốn đưa vào release |

Một số commit gộp nhiều thay đổi: `5555245` gồm #135/#140/#39; `b1a3efd` gồm luồng AI và quyền task; `5652db8` gồm kiểm kê và tài liệu API. Không coi mỗi commit tương đương một issue độc lập khi tách phạm vi. Đội dev cần đọc diff và giữ các phụ thuộc liên quan.

**#109 chờ key; #124 chờ DB bài thật; #113 và #112 chưa triển khai.** Nếu giữ code chuẩn bị #109/#124 trong release, PR phải ghi đúng trạng thái và xác nhận cấu hình không cung cấp tính năng ngoài phạm vi đã duyệt. Nếu tách chúng ra, cập nhật mô tả PR và checklist theo diff cuối cùng.

## 3. Đưa nhánh lên GitHub qua fork

Các lệnh dưới là hướng dẫn cho người thực hiện sau khi thống nhất phạm vi. Chạy từ checkout review và kiểm tra output từng bước.

```powershell
Set-Location 'D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113'
git status --short
git branch --show-current
git remote -v
gh auth status
git fetch origin
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
```

Chỉ gửi các commit đã review. Các file cấu hình local, DB dump, tài khoản và token QA được giữ trong môi trường nội bộ, không đưa vào PR công khai.

Lần đầu tạo fork/remote:

```powershell
gh repo fork tinkerpartners/hncode-edu --clone=false --remote=false
git remote add fork https://github.com/tungfint/hncode-edu.git
git push -u fork mrtee-hncode-v1.0.0-rc.2
gh pr create --repo tinkerpartners/hncode-edu --base main --head tungfint:mrtee-hncode-v1.0.0-rc.2 --draft --title "MrTee HNCode v1.0.0-rc.2: backlog, IDE, API và UI" --body-file docs/handoff-2026-11-14/PR_DRAFT.md
```

Nếu fork hoặc remote đã có, kiểm tra repository đích và bỏ qua bước tạo tương ứng. Nếu tài khoản thực hiện khác tungfint, thay tên fork và `--head` cho đúng. Maintainer có quyền push có thể gửi nhánh vào origin và dùng `--head mrtee-hncode-v1.0.0-rc.2`.

Liên kết các issue bằng `Refs #...` trong mô tả. Khi còn hạng mục chờ điều kiện/nghiệm thu, dùng liên kết tham chiếu thông thường để tránh tự động đóng issue khi merge.

## 4. Review và kiểm thử

1. Gán người review nhóm Grades/Excel, IDE/strict contest, API/quyền và phần chuẩn bị AI/kiểm kê.
2. Dựng môi trường QA; ghi SHA, dataset và phiên bản phần mềm vào [biên bản](01_KIEM_THU_VA_NGHIEM_THU.md).
3. Chạy check, migration plan, bộ test và checklist thủ công. Đính kèm log, ảnh và XLSX của lượt test của team.
4. Nghiệm thu bản sửa định dạng % Excel RC2 bằng Microsoft Excel; nghiệm thu Reset; đo toàn trang Courses và hoàn tất các case Grades/API tương ứng phạm vi release.
5. Khi có commit sửa hoặc conflict, kiểm tra diff cuối và chạy lại nhóm test ảnh hưởng, bộ test tổng trên SHA dự kiến merge.
6. Ghi rõ các phần được hoãn và lý do; maintainer quyết định chuyển Ready for review và merge sau khi đạt phạm vi đã thống nhất.

Workflow `.github/workflows/init.yml` hiện là lint và lọc branch `master`, trong khi PR đích là `main`. Workflow này không cung cấp bằng chứng chạy bộ Django test cho PR đích main. Team cần đính kèm kết quả thực tế hoặc cấu hình CI phù hợp trong một thay đổi được duyệt. Không suy ra test đạt từ việc PR không báo lỗi CI.

## 5. Checklist trước khi deploy

- [ ] Ghi số PR, SHA đã merge, SHA đang chạy và người phụ trách.
- [ ] Release đã được team nghiệm thu theo phạm vi; không mở rộng thành nghiệm thu #109/#124 khi còn thiếu điều kiện.
- [ ] Đối chiếu `.venv`, DB, cache, broker, judge, event daemon và cấu hình staging/VPS thực tế.
- [ ] Sao lưu DB, media, problem data và cấu hình; ghi nơi lưu, checksum và cách khôi phục. Bộ cài local có backup, nhưng chưa diễn tập restore hoàn chỉnh.
- [ ] Cài openpyxl từ requirements; kiểm tra migration plan của **diff release cuối**, dù nhánh bàn giao hiện không thêm migration.
- [ ] Build SCSS/static. Rà và cập nhật cache-bust token cho CSS/JS bị sửa, nhất là style/course.css; kiểm tra nội dung qua cả origin và CDN.
- [ ] Chọn thời điểm phù hợp cho thay đổi strict IDE, tránh cập nhật giữa ca thi đang giám sát; ghi cách xử lý phiên trình duyệt cũ.
- [ ] Token Cầy Cốt được tạo riêng, hash và scope được duyệt trên môi trường đích. API key AI, nếu có, cấu hình qua kênh secret của môi trường.
- [ ] Có kế hoạch kiểm tra sau deploy và rollback code/dữ liệu tương ứng.

Không chuyển DB QA, tài khoản học sinh giả, token test hay mật khẩu của bộ cài Windows sang production. Nếu dùng dữ liệu production để staging, team xác định phạm vi sao chép và quyền truy cập trước khi nhập.

## 6. Trình tự VPS tham khảo

Đường dẫn và tên dịch vụ dưới đây lấy từ `deploy/README.md`, là cấu hình tham khảo trong repo. Ops xác nhận trên máy thật trước khi chạy. Gói bàn giao chưa xác nhận trạng thái VPS hiện tại.

Trước khi cập nhật, ghi SHA hiện tại, kiểm tra trạng thái working tree và hoàn tất backup. Nếu có chỉnh sửa local trên server, dừng bước cập nhật để đối chiếu và bảo toàn cấu hình.

```bash
cd /root/green-oj
git status --short
git rev-parse HEAD
git fetch origin
git log --oneline HEAD..origin/main
git diff --stat HEAD..origin/main
```

Sau khi xác nhận origin/main là release đã được duyệt:

```bash
git merge --ff-only origin/main
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate --plan
```

Ops đọc migration plan và thực hiện bước cập nhật:

```bash
.venv/bin/python manage.py migrate
./make_style.sh
.venv/bin/python manage.py collectstatic --noinput
supervisorctl restart green-site green-bridged green-celery
supervisorctl status
```

Ghi lại SHA thực tế sau cập nhật. Ba dịch vụ site, bridge và celery cần tải code mới. Cấu hình Node/event daemon hoặc judge chỉ cập nhật theo diff/config release đã duyệt.

Smoke test sau deploy: đăng nhập; mở khóa/Lesson/Contest; đổi bài/ngôn ngữ trong IDE; nộp bài QA được phép qua judge; xem live result/Grades; xuất XLSX bằng tài khoản có quyền; kiểm tra từ chối quyền; kiểm tra API đúng token/scope. Kiểm tra strict IDE bằng contest thử được cấp riêng. Theo dõi log site/bridge/celery, HTTP 5xx, trạng thái judge và độ trễ. Ghi kết quả vào biên bản release.

## 7. Rollback

Ưu tiên revert thay đổi bằng PR trên main, kiểm thử bản revert và deploy commit mới. Nếu hệ thống đã quản lý các thư mục release độc lập, Ops có thể chuyển về release trước theo quy trình đã diễn tập, đồng bộ code/static/worker.

`git merge --ff-only <tag-cũ>` không đưa working tree đang ở commit mới về release cũ. Kế hoạch rollback cần một trong các cơ chế thực sự ở trên, kèm quyết định xử lý DB nếu release cuối có migration hoặc ghi dữ liệu mới. Khôi phục SQL phải tính đến dữ liệu phát sinh sau backup và được người phụ trách chấp thuận.

Sau rollback, khởi động lại các dịch vụ liên quan, kiểm tra SHA/static và chạy lại smoke test. Ghi nguyên nhân, ảnh hưởng dữ liệu và liên kết issue phát sinh.
