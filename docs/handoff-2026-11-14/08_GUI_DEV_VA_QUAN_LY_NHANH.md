# Gửi dev và quản lý nhánh mrtee-hncode

## 1. Nhánh ở đâu, đẩy lên đâu?

Nhánh **`mrtee-hncode-v1.0.0-rc.1` đã được tạo ở local**, tại checkout review:

```text
D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113
```

Nó kế thừa toàn bộ code/tài liệu của `codex/backlog-20261113`; nhánh cũ vẫn được giữ. Bộ cài đang chạy ở `D:\AI-Code\Build_HNCode` có cấu hình local riêng, không dùng working tree đó để gom file và push.

Đích ưu tiên là **repo chính `tinkerpartners/hncode-edu`, dưới nhánh `mrtee-hncode-v1.0.0-rc.1`**, do dev có quyền `WRITE`/maintain/admin push. Tài khoản đang dùng `tungfint` chỉ có `READ`, nên chưa thể đẩy trực tiếp vào đó. Chưa có nhánh mới trên GitHub và chưa có Draft PR mới.

Phương án thay thế là tạo fork dưới tài khoản có quyền tạo fork, push nhánh vào fork rồi mở PR về `tinkerpartners/hncode-edu:main`. Không cần cấp quyền production/VPS để review code. Lượt này chưa tạo fork hoặc thay đổi quyền repository.

## 2. Chủ dự án gửi gì cho dev lead?

Gửi **một file `HNCODE_MRTEE_v1.0.0-rc.1_BAN_GIAO.zip`**, gồm:

- `README.md`: điểm bắt đầu và cách nhận bản bàn giao.
- `RELEASE_MANIFEST.json`: branch, base SHA, HEAD SHA và mốc QA.
- `mrtee-hncode-v1.0.0-rc.1.bundle`: lịch sử Git của nhánh từ base, dùng để nhập nhánh và giữ commit.
- `mrtee-hncode-v1.0.0-rc.1.patch`: phương án dự phòng, diff đầy đủ từ base, có binary evidence.
- `docs/`: tổng kết, checklist, mô tả PR, hướng dẫn Git/VPS, ảnh và log.
- `CHANGELOG.txt`, `DIFFSTAT.txt`, `SHA256SUMS.txt`: lịch sử, phạm vi và checksum.

Đây là gói bổ sung cho repository nguồn. Dev clone repo chính rồi nhập bundle; không cần copy thư mục `.git` hoặc toàn bộ Docker local của máy chủ dự án.

Mẫu tin nhắn để anh tự gửi:

> Nhờ team nhận bản mrtee-hncode v1.0.0-rc.1 trong ZIP đính kèm. Đọc `docs/handoff-2026-11-14/00_TONG_KET_BAN_GIAO_RC1.md`, nhập Git bundle thành nhánh `mrtee-hncode-v1.0.0-rc.1`, push lên repo chính và mở Draft PR vào main. Review code, chạy lại test trên SHA của team và trả biên bản theo checklist trước khi merge/VPS. Chú ý lỗi % Excel #140 và các phần còn nghiệm thu; #109 thiếu API key, #124 thiếu DB bài thật, #113/#112 chưa triển khai. Nhánh có nội dung #141/#142, cần thống nhất thứ tự merge. Mật khẩu/token QA cấp riêng, không nằm trong ZIP.

## 3. Dev nhập bundle và mở Draft PR

Giải nén ZIP, đặt gói ở vị trí mình kiểm soát; các đường dẫn dưới là ví dụ trên ổ D. Nếu đã có clone làm việc, dùng clone sạch hoặc kiểm tra thay đổi đang có trước khi nhận nhánh. Không cần chạy lệnh reset/xóa working tree.

```powershell
git clone https://github.com/tinkerpartners/hncode-edu.git D:\AI-Code\HNCode-Review
Set-Location D:\AI-Code\HNCode-Review
git status --short
git fetch origin
git bundle verify D:\BanGiao\mrtee-hncode-v1.0.0-rc.1.bundle
git fetch D:\BanGiao\mrtee-hncode-v1.0.0-rc.1.bundle mrtee-hncode-v1.0.0-rc.1:mrtee-hncode-v1.0.0-rc.1
git switch mrtee-hncode-v1.0.0-rc.1
git rev-parse HEAD
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --check
```

Đối chiếu `git rev-parse HEAD` với `RELEASE_MANIFEST.json`. Bundle có prerequisite base `e665d7b3642478fbeb102b45411ad56d59197a3f`; clone repo chính có lịch sử tương ứng sẽ đáp ứng điều kiện này. Clone shallow cần lấy thêm lịch sử trước khi nhập.

Dev có quyền ghi ở repo chính:

```powershell
git push -u origin mrtee-hncode-v1.0.0-rc.1
gh pr create --repo tinkerpartners/hncode-edu --base main --head mrtee-hncode-v1.0.0-rc.1 --draft --title "MrTee HNCode v1.0.0-rc.1: backlog, IDE, API và UI" --body-file docs/handoff-2026-11-14/PR_DRAFT.md
```

Đọc diff trước khi chạy lệnh push/PR; đây là các bước để dev thực hiện, chưa được chạy trong lượt bàn giao. Nếu nhánh tên này đã tồn tại ở remote tại lúc nhận, đối chiếu SHA và thống nhất với dev lead trước khi cập nhật; không force-push để ghi đè nhánh của người khác.

## 4. Nếu gửi qua fork

Người thực hiện đăng nhập GitHub đúng tài khoản, kiểm tra đã có fork/remote chưa. Với tài khoản `tungfint`, các lệnh tương ứng là:

```powershell
gh repo fork tinkerpartners/hncode-edu --clone=false --remote=false
git remote add fork https://github.com/tungfint/hncode-edu.git
git push -u fork mrtee-hncode-v1.0.0-rc.1
gh pr create --repo tinkerpartners/hncode-edu --base main --head tungfint:mrtee-hncode-v1.0.0-rc.1 --draft --title "MrTee HNCode v1.0.0-rc.1: backlog, IDE, API và UI" --body-file docs/handoff-2026-11-14/PR_DRAFT.md
```

Nếu fork/remote đã có, dùng lại đúng đích thay vì tạo trùng. Nếu dùng tài khoản khác, thay tên chủ fork và `--head`. Sau khi PR tồn tại, gửi **link PR** cho dev lead; link PR là nơi tập trung review, test và trao đổi. Không gửi link branch/PR dự đoán khi chưa tạo thành công.

## 5. Vai trò và quy trình quản lý

| Vai trò | Việc cần làm | Đầu ra |
|---|---|---|
| Chủ dự án | Gửi ZIP/link PR; xác định phạm vi ưu tiên; cung cấp key/DB khi có | Phạm vi release và điều kiện nghiệp vụ |
| Dev lead | Nhập/push nhánh, mở Draft PR; đối chiếu #141/#142; chia reviewer | PR, người phụ trách và danh sách tồn đọng |
| Dev theo nhóm | Review/sửa Grades/Excel, IDE/UI, API/quyền và AI/inventory | Commit và liên kết issue/case |
| QA | Dựng môi trường, chạy test/checklist, ghi SHA và evidence | Biên bản PASS/FAIL/BLOCKED/NOT RUN |
| Maintainer | Duyệt phạm vi và diff cuối, xử lý phụ thuộc, quyết định merge | SHA merge đã duyệt |
| Ops | Backup, staging/VPS, restart site/bridge/worker, smoke test/rollback | Biên bản triển khai |

Quy trình: **nhận nhánh → Draft PR → review/sửa → QA trên SHA cuối → maintainer merge → staging/VPS**. Không tự đóng issue còn thiếu điều kiện. Mô tả PR dùng `Refs #...`; chỉ dùng từ khóa tự đóng khi team xác nhận hoàn tất issue tương ứng.

Các commit sửa tiếp theo có thể đẩy vào cùng nhánh trong lúc review. Khi cần chốt bản bàn giao mới, tạo `mrtee-hncode-v1.0.0-rc.2`, cập nhật manifest/changelog và kết quả test. Không gắn tag `v1.0.0` chính thức cho đến khi maintainer thống nhất quy ước release.
