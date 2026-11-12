import argparse
import csv
import ftplib
import json
import mimetypes
import re
import socket
from datetime import datetime
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
FTP_UPLOAD_ERRORS = ftplib.all_errors + (OSError, TimeoutError, socket.timeout)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path, rows, fieldnames):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def posix_join(*parts):
    cleaned = []
    absolute = False
    for index, part in enumerate(parts):
        if part is None:
            continue
        text = str(part).replace("\\", "/").strip("/")
        if index == 0 and str(part).startswith("/"):
            absolute = True
        if text:
            cleaned.append(text)
    joined = "/".join(cleaned)
    return "/" + joined if absolute else joined


def public_join(prefix, *parts):
    return posix_join(prefix or "", *parts)


def absolute_url(domain, path):
    domain = normalize_public_domain(domain)
    path = public_join(path)
    if not domain:
        return path
    return domain + path


def normalize_public_domain(domain):
    domain = (domain or "").strip().rstrip("/")
    markdown = re.search(r"\]\((https?://[^)]+)\)", domain)
    if markdown:
        domain = markdown.group(1)
    else:
        plain = re.search(r"https?://[^\s<>\]\)]+", domain)
        if plain:
            domain = plain.group(0)
    domain = domain.strip().strip("<>").rstrip("/.,;")
    if not domain:
        return ""
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain


def safe_remote_name(local_path, suffix):
    stem = local_path.stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "image"
    return f"{stem}_{suffix}{local_path.suffix.lower()}"


def webp_alias_name(remote_name):
    return str(Path(remote_name).with_suffix(".webp")).replace("\\", "/")


def default_output_dir(sources):
    if not sources:
        return Path.cwd()
    first = Path(sources[0])
    if first.is_dir():
        return first
    if first.is_file():
        return first.parent
    return Path.cwd()


def resolve_upload_root(config):
    legacy_root = str(config.get("remote_upload_root", "")).strip()
    if legacy_root:
        return legacy_root
    return posix_join(
        config.get("remote_base_dir", "/D:/wwwroot"),
        config.get("customer_directory", ""),
        config.get("uploads_directory", "uploads"),
    )


def resolve_date_dir(args, config):
    if args.month:
        return args.month.strip().strip("/\\")
    if not config.get("use_dated_subdir", True):
        return ""
    return datetime.now().strftime(config.get("date_directory_format", "%Y%m"))


def collect_files(sources):
    files = []
    for source in sources:
        path = Path(source)
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            for item in sorted(path.iterdir()):
                if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
                    files.append(item)
            continue
        raise FileNotFoundError(f"找不到上传来源：{path}")

    unique = []
    seen = set()
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def connect(config):
    if not all(str(config.get(key, "")).strip() for key in ("host", "username", "password")):
        raise SystemExit("FTP配置为空或不完整。请向同事索取带完整内容的 ftp_config.json，替换项目根目录的空模板。")
    ftp_cls = ftplib.FTP_TLS if config.get("use_tls") else ftplib.FTP
    ftp = ftp_cls()
    print(f"连接FTP：{config['host']}:{int(config.get('port', 21))}", flush=True)
    ftp.connect(
        config["host"],
        int(config.get("port", 21)),
        timeout=int(config.get("timeout", 30)),
    )
    print("FTP已连接，正在登录……", flush=True)
    ftp.login(config["username"], config["password"])
    ftp.set_pasv(bool(config.get("passive", True)))
    if config.get("use_tls"):
        ftp.prot_p()
    try:
        ftp.sendcmd("OPTS UTF8 ON")
    except ftplib.Error_perm:
        pass
    print("FTP登录成功", flush=True)
    return ftp


def ensure_remote_dir(ftp, remote_dir):
    remote_dir = remote_dir.replace("\\", "/").rstrip("/")
    print(f"检查/创建远程目录：{remote_dir}", flush=True)
    try:
        ftp.cwd(remote_dir)
        print("远程目录已存在", flush=True)
        return
    except ftplib.all_errors:
        pass

    if remote_dir.startswith("/"):
        ftp.cwd("/")
    for part in [p for p in remote_dir.strip("/").split("/") if p]:
        try:
            ftp.cwd(part)
        except ftplib.all_errors:
            try:
                ftp.mkd(part)
            except ftplib.all_errors:
                pass
            ftp.cwd(part)
    print("远程目录已就绪", flush=True)


def upload_file(ftp, local_path, remote_dir, remote_name, dry_run=False):
    remote_path = posix_join(remote_dir, remote_name)
    if dry_run:
        return {
            "local_path": str(local_path),
            "original_filename": local_path.name,
            "filename": remote_name,
            "remote_path": remote_path,
            "size": local_path.stat().st_size,
            "uploaded": False,
            "dry_run": True,
        }

    with local_path.open("rb") as handle:
        ftp.storbinary(f"STOR {remote_name}", handle)

    remote_size = None
    try:
        remote_size = ftp.size(remote_name)
    except ftplib.all_errors:
        pass

    return {
        "local_path": str(local_path),
        "original_filename": local_path.name,
        "filename": remote_name,
        "remote_path": remote_path,
        "size": local_path.stat().st_size,
        "remote_size": remote_size,
        "uploaded": True,
    }


def build_manifest_entry(result, date_dir, config, public_domain):
    local_path = Path(result["local_path"])
    public_path = public_join(config.get("public_url_prefix", "/uploads"), date_dir, result["filename"])
    public_url = absolute_url(public_domain or config.get("public_domain", ""), public_path)
    return {
        **result,
        "date_dir": date_dir,
        "public_path": public_path,
        "public_url": public_url,
        "mime_type": mimetypes.guess_type(local_path.name)[0] or "application/octet-stream",
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Upload article images to the configured FTP uploads directory.")
    parser.add_argument("sources", nargs="*", help="图片文件或包含图片的目录")
    parser.add_argument("--source", action="append", default=[], help="图片文件或目录，可重复")
    parser.add_argument("--config", default="ftp_config.json", help="FTP配置文件，默认ftp_config.json")
    parser.add_argument("--profile", help="选择ftp_config.json中的服务器配置，例如 server1 或 server2；不传使用default_profile")
    parser.add_argument(
        "--customer-directory",
        help="覆盖当前服务器配置中的客户目录，例如 ZheShang_En；不传则使用配置文件默认值",
    )
    parser.add_argument(
        "--remote-upload-root",
        help="直接覆盖远程uploads根目录，例如 /D:/wwwroot/ZheShang_En/uploads；优先级高于客户目录拼接",
    )
    parser.add_argument("--month", help="覆盖配置中的日期目录，如202609；不传则按date_directory_format自动生成")
    parser.add_argument("--manifest", help="输出manifest路径，默认写到第一个上传来源目录")
    parser.add_argument("--mapping-csv", help="输出图片URL映射CSV；默认与manifest同目录")
    parser.add_argument("--public-domain", help="正式域名，如 https://example.com；也可写在ftp_config.json")
    parser.add_argument("--name-suffix", default=datetime.now().strftime("%Y%m%d%H%M%S"), help="远程文件名后缀，默认当前时间戳")
    parser.add_argument("--keep-name", action="store_true", help="保留原始文件名上传；默认会追加时间戳防覆盖")
    parser.add_argument("--no-webp-alias", action="store_true", help="不额外上传同内容的.webp后缀副本；默认会上传")
    parser.add_argument("--retries", type=int, default=3, help="单张图片上传失败后的重试次数，默认3")
    parser.add_argument("--dry-run", action="store_true", help="只检查文件和生成manifest，不连接上传")
    return parser.parse_args()


def close_ftp(ftp):
    if not ftp:
        return
    try:
        ftp.quit()
    except ftplib.all_errors:
        try:
            ftp.close()
        except Exception:
            pass


def reconnect(config, remote_dir):
    ftp = connect(config)
    ensure_remote_dir(ftp, remote_dir)
    return ftp


def select_config_profile(config, profile_name):
    profiles = config.get("profiles")
    if not profiles:
        return config, profile_name or ""
    selected = profile_name or config.get("default_profile") or next(iter(profiles))
    if selected not in profiles:
        available = ", ".join(profiles.keys())
        raise SystemExit(f"找不到FTP配置profile：{selected}；可用：{available}")
    base = {key: value for key, value in config.items() if key not in ("profiles", "default_profile")}
    base.update(profiles[selected])
    base["profile"] = selected
    return base, selected


def main():
    args = parse_args()
    args.retries = max(1, args.retries)
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    config = read_json(config_path)
    config, selected_profile = select_config_profile(config, args.profile)

    if args.customer_directory:
        config["customer_directory"] = args.customer_directory.strip().strip("/\\")
        config["remote_upload_root"] = ""
    if args.remote_upload_root:
        config["remote_upload_root"] = args.remote_upload_root.strip()

    sources = args.sources + args.source
    if not sources:
        raise SystemExit("请指定要上传的图片文件或目录，例如：python deploy_ftp.py --source runs/.../article/selected_images")

    files = collect_files(sources)
    if not files:
        raise SystemExit("没有找到可上传图片。支持：jpg、jpeg、png、webp、gif")

    upload_root = resolve_upload_root(config)
    date_dir = resolve_date_dir(args, config)
    remote_dir = posix_join(upload_root, date_dir)
    entries = []
    public_domain = normalize_public_domain(args.public_domain or config.get("public_domain", ""))

    if selected_profile:
        label = config.get("label") or config.get("host") or selected_profile
        print(f"FTP配置：{selected_profile}（{label}）", flush=True)
    print(f"上传目录：{remote_dir}", flush=True)
    print(f"待处理图片数量：{len(files)}", flush=True)

    if args.dry_run:
        for local_path in files:
            remote_name = local_path.name if args.keep_name else safe_remote_name(local_path, args.name_suffix)
            result = upload_file(None, local_path, remote_dir, remote_name, dry_run=True)
            alias_result = None
            if not args.no_webp_alias:
                alias_name = webp_alias_name(remote_name)
                if alias_name != remote_name:
                    alias_result = upload_file(None, local_path, remote_dir, alias_name, dry_run=True)
            entries.append(build_manifest_entry(result, date_dir, config, public_domain))
            if alias_result:
                entries[-1]["webp_alias"] = build_manifest_entry(alias_result, date_dir, config, public_domain)
    else:
        ftp = connect(config)
        try:
            ensure_remote_dir(ftp, remote_dir)
            for index, local_path in enumerate(files, 1):
                remote_name = local_path.name if args.keep_name else safe_remote_name(local_path, args.name_suffix)
                alias_name = webp_alias_name(remote_name) if not args.no_webp_alias else ""
                result = None
                alias_result = None
                for attempt in range(1, args.retries + 1):
                    try:
                        print(f"[{index}/{len(files)}] 上传：{local_path.name} -> {remote_name}（尝试 {attempt}/{args.retries}）", flush=True)
                        result = upload_file(ftp, local_path, remote_dir, remote_name)
                        if alias_name and alias_name != remote_name:
                            print(f"[{index}/{len(files)}] 上传webp别名：{local_path.name} -> {alias_name}", flush=True)
                            alias_result = upload_file(ftp, local_path, remote_dir, alias_name)
                        break
                    except FTP_UPLOAD_ERRORS as exc:
                        print(f"[{index}/{len(files)}] 上传失败：{type(exc).__name__}: {exc}", flush=True)
                        if attempt >= args.retries:
                            raise
                        print("正在重连FTP后重试当前图片……", flush=True)
                        close_ftp(ftp)
                        ftp = reconnect(config, remote_dir)
                entries.append(build_manifest_entry(result, date_dir, config, public_domain))
                if alias_result:
                    entries[-1]["webp_alias"] = build_manifest_entry(alias_result, date_dir, config, public_domain)
                print(f"[{index}/{len(files)}] 完成：{remote_name}", flush=True)
        finally:
            close_ftp(ftp)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config_file": str(config_path),
        "config_profile": selected_profile,
        "upload_root": upload_root,
        "date_dir": date_dir,
        "remote_dir": remote_dir,
        "public_url_prefix": absolute_url(public_domain, public_join(config.get("public_url_prefix", "/uploads"), date_dir)),
        "count": len(entries),
        "webp_alias_enabled": not args.no_webp_alias,
        "items": entries,
    }

    output_dir = default_output_dir(sources)
    manifest_path = Path(args.manifest) if args.manifest else output_dir / "upload_manifest.json"
    write_json(manifest_path, manifest)
    mapping_path = Path(args.mapping_csv) if args.mapping_csv else manifest_path.with_name("image_url_mapping.csv")
    write_csv(
        mapping_path,
        [
            {
                "local_path": item["local_path"],
                "public_url": item["public_url"],
            }
            for item in entries
        ],
        ["local_path", "public_url"],
    )

    print(f"上传目录：{remote_dir}")
    print(f"图片数量：{len(entries)}")
    print(f"manifest：{manifest_path}")
    print(f"mapping csv：{mapping_path}")
    for item in entries:
        status = "DRY" if item.get("dry_run") else "OK"
        print(f"[{status}] {item['original_filename']} -> {item['filename']} -> {item['public_url']}")
        if item.get("webp_alias"):
            alias = item["webp_alias"]
            print(f"[{status}] webp副本：{item['original_filename']} -> {alias['filename']} -> {alias['public_url']}")


if __name__ == "__main__":
    main()
