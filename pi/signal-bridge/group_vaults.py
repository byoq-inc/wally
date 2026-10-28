#!/usr/bin/env python3
"""Descriptor-relative, fail-closed private Signal group vault support."""
from __future__ import annotations

import base64, errno, fcntl, hashlib, json, os, re, secrets, stat, subprocess, tempfile, time
from pathlib import Path
from typing import Any

VAULT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXPECTED_VAULT_IDS = ("govfiles", "frnk", "personal-admin", "system-automation", "research", "byoq", "harness-lab", "experimental-research-agents", "notifications", "alerts")
MEMORY_FILE, SUMMARY_FILE, PROMOTED_FILE = "MEMORY.md", "SUMMARY.md", "PROMOTED.md"
INSTRUCTIONS_FILE = "INSTRUCTIONS.md"
_VAULT_COMMAND_IDS = "|".join(re.escape(value) for value in EXPECTED_VAULT_IDS)
COMMAND_RE = re.compile(rf"/vault ({_VAULT_COMMAND_IDS}) (memory|skills)\Z")

class VaultError(RuntimeError): pass

def write_all(fd:int,data:bytes)->None:
    """Write a complete durable payload or fail; tolerate EINTR and short writes."""
    view=memoryview(data)
    written=0
    while written < len(view):
        try: count=os.write(fd,view[written:])
        except OSError as exc:
            if exc.errno==errno.EINTR: continue
            raise
        if count is None or count <= 0: raise OSError(errno.EIO,"write made no progress")
        written += count

def _parts(*values: str) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or Path(value).is_absolute(): raise VaultError("invalid vault path")
        bits = Path(value).parts
        if any(x in ("", ".", "..") for x in bits): raise VaultError("invalid vault path")
        out.extend(bits)
    return tuple(out)

class VaultFS:
    """All vault operations are anchored to a non-symlink root descriptor."""
    def __init__(self, root: Path, create: bool = False):
        raw = root.expanduser()
        try:
            if stat.S_ISLNK(os.lstat(raw).st_mode): raise VaultError("vault root must not be a symlink")
        except FileNotFoundError:
            if not create: raise VaultError("vault root does not exist")
            raw.mkdir(parents=True, mode=0o700)
        if stat.S_ISLNK(os.lstat(raw).st_mode): raise VaultError("vault root must not be a symlink")
        self.root = raw.absolute()
        self.fd = os.open(raw, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    def close(self): os.close(self.fd)
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def dirfd(self, parts: tuple[str, ...], create=False) -> int:
        fd = os.dup(self.fd)
        try:
            for name in parts:
                if create:
                    try: os.mkdir(name, 0o700, dir_fd=fd)
                    except FileExistsError: pass
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = nxt
                if not stat.S_ISDIR(os.fstat(fd).st_mode): raise VaultError("vault component is not a directory")
            return fd
        except (OSError, VaultError) as exc:
            os.close(fd)
            if isinstance(exc, VaultError): raise
            raise VaultError(f"unsafe vault directory: {exc}") from exc
    def mkdirs(self, *parts: str):
        fd=self.dirfd(_parts(*parts), True); os.fchmod(fd,0o700); os.fsync(fd); os.close(fd)
    def read(self, *parts: str, limit: int|None=None) -> bytes:
        p=_parts(*parts); d=self.dirfd(p[:-1])
        try:
            fd=os.open(p[-1], os.O_RDONLY|os.O_NOFOLLOW, dir_fd=d)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise VaultError("vault content is not a regular file")
                data=os.read(fd, (limit+1) if limit is not None else os.fstat(fd).st_size+1)
                return data
            finally: os.close(fd)
        except OSError as exc:
            if exc.errno == 40: raise VaultError("symlink is not allowed in vault") from exc
            raise VaultError(f"unsafe vault file: {exc}") from exc
        finally: os.close(d)
    def exists(self,*parts:str)->bool:
        try: self.read(*parts); return True
        except VaultError as exc:
            if "No such file" in str(exc): return False
            raise
    def write_new(self, data: bytes, *parts: str, mode=0o600)->bool:
        p=_parts(*parts); d=self.dirfd(p[:-1])
        try:
            try: fd=os.open(p[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=d)
            except FileExistsError: return False
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise VaultError("vault content is not a regular file")
                write_all(fd,data); os.fchmod(fd,mode); os.fsync(fd)
            finally: os.close(fd)
            os.fsync(d); return True
        finally: os.close(d)
    def replace(self,data:bytes,*parts:str,mode=0o600):
        p=_parts(*parts); d=self.dirfd(p[:-1]); tmp=f".{p[-1]}.tmp-{secrets.token_hex(12)}"
        staged=False
        try:
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=d)
            staged=True
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise VaultError("staged vault content is not a regular file")
                write_all(fd,data); os.fchmod(fd,mode); os.fsync(fd)
            finally: os.close(fd)
            os.replace(tmp,p[-1],src_dir_fd=d,dst_dir_fd=d); staged=False; os.fsync(d)
        except BaseException:
            if staged:
                try: os.unlink(tmp,dir_fd=d)
                except FileNotFoundError: pass
            raise
        finally: os.close(d)
    def append(self,data:bytes,*parts:str,mode=0o600):
        p=_parts(*parts); d=self.dirfd(p[:-1])
        try:
            fd=os.open(p[-1],os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,mode,dir_fd=d)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise VaultError("vault content is not a regular file")
                write_all(fd,data); os.fchmod(fd,mode); os.fsync(fd)
            finally: os.close(fd)
            os.fsync(d)
        finally: os.close(d)

def safe_path(root:Path,*parts:str,must_exist=False)->Path:
    """Compatibility path return; safety-sensitive I/O uses VaultFS descriptors."""
    raw=root.expanduser()
    if stat.S_ISLNK(os.lstat(raw).st_mode): raise VaultError("vault root must not be a symlink")
    with VaultFS(raw) as fs:
        p=_parts(*parts)
        fd=os.dup(fs.fd)
        try:
            for index,name in enumerate(p):
                try: info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                except FileNotFoundError:
                    if must_exist: raise VaultError("vault path does not exist")
                    break
                if stat.S_ISLNK(info.st_mode): raise VaultError("symlink is not allowed in vault")
                if index < len(p)-1:
                    nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd); os.close(fd); fd=nxt
        finally: os.close(fd)
    return raw.absolute().joinpath(*_parts(*parts))

def _bounded(data:bytes,max_bytes:int)->tuple[str,bool]:
    if max_bytes<1: raise VaultError("context limit must be positive")
    truncated=len(data)>max_bytes; marker=b"\n[Vault content truncated.]"
    payload=data[:max_bytes] if not truncated else data[:max(0,max_bytes-len(marker))]+marker[:max_bytes]
    return payload[:max_bytes].decode("utf-8",errors="ignore"),truncated

class GroupVaults:
    def __init__(self,config:dict[str,Any]):
        c=config.get("vaults")
        if not isinstance(c,dict) or not c.get("enabled"): raise VaultError("group vaults are not enabled")
        self.fs=VaultFS(Path(c.get("root",""))); self.root=self.fs.root
        self.max_context_bytes=self._positive(c.get("max_context_bytes",32768),"max_context_bytes")
        self.max_retrieval_bytes=self._positive(c.get("max_retrieval_bytes",32768),"max_retrieval_bytes")
        groups=c.get("groups")
        if not isinstance(groups,dict) or set(groups)!=set(EXPECTED_VAULT_IDS): raise VaultError("vault mapping must define exactly the approved vault IDs")
        self.by_group={}; self.labels={}; self.extensions_by_vault={}
        for v in EXPECTED_VAULT_IDS:
            e=groups[v]; gid=e.get("signal_group_id") if isinstance(e,dict) else None; label=e.get("label") if isinstance(e,dict) else None
            if not isinstance(gid,str) or not gid or gid in self.by_group: raise VaultError("each vault requires a unique exact Signal group ID")
            if not isinstance(label,str) or not label.strip() or len(label)>80: raise VaultError("each vault requires a short label")
            extensions=e.get("extensions",[]) if isinstance(e,dict) else []
            if not isinstance(extensions,list) or len(extensions)>32: raise VaultError("group extensions must be a list of at most 32 entries")
            parsed=[]; seen=set()
            for extension in extensions:
                if not isinstance(extension,dict) or set(extension)!={"path","sha256"}: raise VaultError("each group extension requires exactly path and sha256")
                path=extension.get("path"); digest=extension.get("sha256")
                if not isinstance(path,str) or not path.startswith("extensions/"): raise VaultError("group extension paths must be relative to the vault extensions directory")
                parts=_parts(path)
                if parts[0]!="extensions" or Path(parts[-1]).suffix not in {".ts",".js",".mjs",".cjs"}: raise VaultError("group extension must be a TypeScript or JavaScript file under extensions")
                if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest): raise VaultError("group extension requires a lowercase SHA-256 digest")
                if parts in seen: raise VaultError("duplicate group extension path")
                seen.add(parts); parsed.append((parts,digest))
            d=self.fs.dirfd(("groups",v)); os.close(d); self.by_group[gid]=v; self.labels[v]=label.strip(); self.extensions_by_vault[v]=parsed
    def __del__(self):
        try: self.fs.close()
        except Exception: pass
    @staticmethod
    def _positive(v,n):
        if isinstance(v,bool) or not isinstance(v,int) or v<1: raise VaultError(f"{n} must be a positive integer")
        return v
    def vault_dir(self,vault_id,must_exist=True):
        if vault_id not in EXPECTED_VAULT_IDS: raise VaultError("unknown vault")
        if must_exist: d=self.fs.dirfd(("groups",vault_id)); os.close(d)
        return self.root/"groups"/vault_id
    def group_vault_id(self,gid):
        if not isinstance(gid,str) or gid not in self.by_group: raise VaultError("Signal group is not mapped to a private vault")
        return self.by_group[gid]
    def skill_paths(self,gid):
        result=[self.root/"shared"/"skills"]
        if gid is not None: result.append(self.root/"groups"/self.group_vault_id(gid)/"skills")
        for p in result: d=self.fs.dirfd(tuple(p.relative_to(self.root).parts)); os.close(d)
        return result
    def extension_paths(self,gid):
        vault_id=self.group_vault_id(gid)
        result=[]
        for parts,expected in self.extensions_by_vault[vault_id]:
            data=self.fs.read("groups",vault_id,*parts,limit=5*1024*1024)
            if len(data)>5*1024*1024: raise VaultError(f"group extension exceeds 5 MiB: {'/'.join(parts)}")
            if hashlib.sha256(data).hexdigest()!=expected: raise VaultError(f"group extension integrity mismatch: {'/'.join(parts)}")
            result.append(self.root.joinpath("groups",vault_id,*parts))
        return result
    def compose_context(self,gid,query=None):
        if query is not None and gid is not None:
            return self.compose_search_context(gid, query)
        sections=[("Shared memory",("shared",MEMORY_FILE))]
        if self.fs.exists("shared",SUMMARY_FILE): sections.append(("Shared summary",("shared",SUMMARY_FILE)))
        if self.fs.exists("shared",INSTRUCTIONS_FILE): sections.append(("Shared instructions",("shared",INSTRUCTIONS_FILE)))
        if self.fs.exists("shared",PROMOTED_FILE): sections.append(("Explicitly reviewed global promotions",("shared",PROMOTED_FILE)))
        if gid is None: sections += [(f"Private vault reference: {v}",( "groups",v,SUMMARY_FILE)) for v in EXPECTED_VAULT_IDS]
        else:
            vault_id=self.group_vault_id(gid)
            sections += [("This profile's memory",("groups",vault_id,MEMORY_FILE)),
                         ("This profile's summary",("groups",vault_id,SUMMARY_FILE))]
            if self.fs.exists("groups",vault_id,INSTRUCTIONS_FILE):
                sections.append(("This profile's instructions",("groups",vault_id,INSTRUCTIONS_FILE)))
        prefix="[Deterministic private vault context]"
        remain=max(0,self.max_context_bytes-len(prefix.encode())); out=[prefix]
        headers=[f"\n## {title}\n" for title,_ in sections]
        for index,(title,p) in enumerate(sections):
            h=headers[index]; hb=len(h.encode())
            if hb>remain: break
            remain-=hb
            reserved=sum(len(value.encode()) for value in headers[index+1:])
            content_limit=max(0,remain-reserved)
            text,tr=_bounded(self.fs.read(*p,limit=content_limit),content_limit) if content_limit else ("",True)
            out += [h,text]; remain-=len(text.encode())
            if remain<=0: break
        return "".join(out).encode()[:self.max_context_bytes].decode("utf-8",errors="ignore")
    def compose_search_context(self,gid,query):
        from memory_search import DATE_POLICY, mandatory_policy, search, visible_instructions, optional_fact_hashes
        vault_id=self.group_vault_id(gid)
        memory=self.fs.read('groups',vault_id,MEMORY_FILE).decode('utf-8')
        summary=self.fs.read('groups',vault_id,SUMMARY_FILE).decode('utf-8')
        instructions=(self.fs.read('groups',vault_id,INSTRUCTIONS_FILE).decode('utf-8')
                      if self.fs.exists('groups',vault_id,INSTRUCTIONS_FILE) else '')
        fixed=['[Deterministic private vault context]', DATE_POLICY]
        for p in [('shared',MEMORY_FILE),('shared',SUMMARY_FILE),('shared',INSTRUCTIONS_FILE),('shared',PROMOTED_FILE)]:
            if self.fs.exists(*p):
                fixed.append(f"## {'/'.join(p)}\n"+self.fs.read(*p).decode('utf-8'))
        fixed += ['## This profile instructions\n'+visible_instructions(instructions),
                  '## Mandatory operating context (unreviewed assertions remain mandatory)\n'+mandatory_policy(memory,instructions),
                  '## This profile summary\n'+summary]
        base='\n\n'.join(fixed)
        remaining=self.max_context_bytes-len(base.encode())
        if remaining<0:
            raise VaultError('mandatory context exceeds configured budget; no policies were silently omitted')
        # Rebuild from current canonical optional assertions. Everything else
        # is already present above: do not duplicate mandatory facts in snippets.
        hits=search([(MEMORY_FILE,memory)],query,max_bytes=min(10000,remaining),
                    optional_hashes=optional_fact_hashes(instructions))
        if hits:
            return base+hits
        fallback='\nNo optional lexical memory matches. Mandatory context and summary above remain authoritative. Consult this vault MEMORY.md for a refined query; never infer facts from another vault.\n'
        return base+(fallback if len(fallback.encode()) <= remaining else '')

    def retrieve(self,vault_id,kind):
        self.vault_dir(vault_id)
        if kind=="memory": return _bounded(self.fs.read("groups",vault_id,MEMORY_FILE,limit=self.max_retrieval_bytes),self.max_retrieval_bytes)[0]
        if kind=="skills":
            chunks=[]; remaining=self.max_retrieval_bytes
            def walk(fd:int,prefix:str=""):
                nonlocal remaining
                for name in sorted(os.listdir(fd)):
                    try: child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                    except OSError as exc:
                        if exc.errno==40: raise VaultError("symlink is not allowed in skills") from exc
                        raise
                    try:
                        mode=os.fstat(child).st_mode; rel=f"{prefix}{name}"
                        if stat.S_ISDIR(mode): walk(child,rel+"/")
                        elif stat.S_ISREG(mode) and name=="SKILL.md" and remaining>32:
                            header=f"## {rel}\n"; data=os.read(child,min(remaining,os.fstat(child).st_size+1)); text,_=_bounded(data,max(1,remaining-len(header.encode())))
                            chunks.extend((header,text,"\n")); remaining-=len((header+text+"\n").encode())
                    finally: os.close(child)
            fd=self.fs.dirfd(("groups",vault_id,"skills"))
            try: walk(fd)
            finally: os.close(fd)
            return "".join(chunks) if chunks else "No group-local skills are installed."
        raise VaultError("retrieval kind must be memory or skills")
    def audit(self,action,vault_id,detail="",actor="bridge",request_id=""):
        if action not in {"retrieve","promotion-request","promotion-approve","promotion-apply"}: raise VaultError("invalid audit action")
        rec={"time":int(time.time()),"action":action,"vault_id":vault_id,"actor":actor[:80],"request_id":request_id[:80],"detail":detail[:120]}
        self.fs.append((json.dumps(rec,sort_keys=True,separators=(",",":"))+"\n").encode(),"admin","audit.jsonl")

def retrieval_command(text):
    if not isinstance(text,str) or not text.lower().startswith("/vault"): return None
    m=COMMAND_RE.fullmatch(text)
    if not m: raise VaultError(f"Usage: /vault <{'|'.join(EXPECTED_VAULT_IDS)}> <memory|skills>")
    return m.group(1),m.group(2)

def _backup(fs:VaultFS,data:bytes,label:str)->str:
    fs.mkdirs("backups"); name=f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{time.time_ns()}-{secrets.token_hex(8)}-{label}"
    if not fs.write_new(data,"backups",name): raise VaultError("exclusive backup collision")
    return name

def _admin_audit(fs:VaultFS,action:str,vault_id:str,actor:str,request_id:str,detail:str=""):
    rec={"time":int(time.time()),"action":action,"vault_id":vault_id,"actor":actor[:80],"request_id":request_id[:80],"detail":detail[:120]}
    fs.append((json.dumps(rec,sort_keys=True,separators=(",",":"))+"\n").encode(),"admin","audit.jsonl")

def bootstrap(root:Path,imports:dict[str,Path]|None=None)->list[str]:
    changed=[]
    with VaultFS(root,create=True) as fs:
        for d in (("groups",),("shared",),("shared","skills"),("admin",),("backups",)): fs.mkdirs(*d)
        fs.write_new(b"# Shared memory\n\nNo shared memory has been promoted.\n","shared",MEMORY_FILE)
        for v in EXPECTED_VAULT_IDS:
            fs.mkdirs("groups",v); fs.mkdirs("groups",v,"skills"); fs.mkdirs("groups",v,"extensions"); fs.mkdirs("groups",v,"runtime","sessions")
            src=(imports or {}).get(v)
            if src is not None:
                if stat.S_ISLNK(os.lstat(src).st_mode): raise VaultError(f"invalid import source for {v}")
                sfd=os.open(src,os.O_RDONLY|os.O_NOFOLLOW)
                try: data=os.read(sfd,os.fstat(sfd).st_size+1)
                finally: os.close(sfd)
                if fs.write_new(data,"groups",v,MEMORY_FILE): changed.append(f"{v} memory imported")
            elif fs.write_new(f"# Private memory\n\nPrivate vault `{v}` is initialized.\n".encode(),"groups",v,MEMORY_FILE): changed.append(f"{v} memory initialized")
            size=len(fs.read("groups",v,MEMORY_FILE)); summary=f"Private vault `{v}` is available by explicit retrieval. Memory bytes: {size}. Skills: undisclosed.\n".encode()
            if fs.write_new(summary,"groups",v,SUMMARY_FILE): changed.append(f"{v} minimized summary initialized")
    return changed

def _open_key(path:Path, private:bool, principal_uid:int|None=None)->tuple[int,bytes]:
    """Open a key through validated descriptors and pin the bytes we inspect."""
    absolute=path.absolute()
    parts=absolute.parts; fd=os.open(parts[0],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in parts[1:-1]:
            nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(nxt)
            if not stat.S_ISDIR(info.st_mode) or (info.st_mode & 0o022 and not (info.st_mode & stat.S_ISVTX and info.st_uid==0)): os.close(nxt); raise VaultError("unsafe signing key ancestor")
            os.close(fd); fd=nxt
        keyfd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
    except OSError as exc:
        raise VaultError("unsafe signing key path") from exc
    finally: os.close(fd)
    info=os.fstat(keyfd); mode=stat.S_IMODE(info.st_mode)
    if not stat.S_ISREG(info.st_mode) or (principal_uid is not None and info.st_uid!=principal_uid) or (private and mode&0o077) or (not private and mode&0o022):
        os.close(keyfd); raise VaultError("signing key has unsafe owner, type, or mode")
    data=b""
    while True:
        chunk=os.read(keyfd,65536)
        if not chunk: break
        data+=chunk
        if len(data)>65536: os.close(keyfd); raise VaultError("signing key is too large")
    os.lseek(keyfd,0,os.SEEK_SET)
    return keyfd,data

def _principal(uid:int|None=None)->str: return f"uid:{os.geteuid() if uid is None else uid}"

def _public_owner(public_key:Path)->str:
    fd,_=_open_key(public_key,False)
    try: return _principal(os.fstat(fd).st_uid)
    finally: os.close(fd)

def _public_identity(public_key:Path)->str:
    fd,data=_open_key(public_key,False)
    try:
        der=subprocess.run(["openssl","pkey","-pubin","-in",f"/proc/self/fd/{fd}","-outform","DER"],check=True,capture_output=True,pass_fds=(fd,)).stdout
    except (OSError,subprocess.CalledProcessError) as exc: raise VaultError("invalid promotion public key") from exc
    finally: os.close(fd)
    return "ed25519:"+hashlib.sha256(der).hexdigest()

def _signature(data:bytes,private_key:Path,public_key:Path)->str:
    # Verification against the separately supplied public key prevents a caller
    # from attaching an unrelated authenticated identity to its signature.
    uid=os.geteuid(); private_fd,_=_open_key(private_key,True,uid); public_fd,_=_open_key(public_key,False,uid)
    try:
        with tempfile.NamedTemporaryFile() as payload:
            payload.write(data); payload.flush()
            signed=subprocess.run(["openssl","pkeyutl","-sign","-rawin","-inkey",f"/proc/self/fd/{private_fd}","-in",payload.name],check=True,capture_output=True,pass_fds=(private_fd,)).stdout
    except (OSError,subprocess.CalledProcessError) as exc: raise VaultError("promotion signing failed") from exc
    finally: os.close(private_fd); os.close(public_fd)
    _verify(data,base64.b64encode(signed).decode("ascii"),public_key)
    return base64.b64encode(signed).decode("ascii")

def _verify(data:bytes,signature:str,public_key:Path):
    try: raw=base64.b64decode(signature,validate=True)
    except (ValueError,TypeError) as exc: raise VaultError("invalid promotion signature") from exc
    public_fd,_=_open_key(public_key,False)
    with tempfile.NamedTemporaryFile() as sig, tempfile.NamedTemporaryFile() as payload:
        sig.write(raw); sig.flush(); payload.write(data); payload.flush()
        try: result=subprocess.run(["openssl","pkeyutl","-verify","-rawin","-pubin","-inkey",f"/proc/self/fd/{public_fd}","-sigfile",sig.name,"-in",payload.name],capture_output=True,pass_fds=(public_fd,))
        except OSError as exc: raise VaultError("promotion verification unavailable") from exc
        finally: os.close(public_fd)
    if result.returncode: raise VaultError("invalid promotion signature")

def _canonical(value:dict[str,Any])->bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":")).encode()

def _external_private_key(root:Path,key:Path):
    try:
        if key.is_symlink() or not key.is_file(): raise VaultError("private signing key must be a regular non-symlink file")
        key.resolve(strict=True).relative_to(root.resolve(strict=True))
    except ValueError: return
    raise VaultError("private signing keys must live outside the vault tree")

def promotion_preview(root:Path,vault_id:str,source:Path,requester_private_key:Path,requester_public_key:Path,expires_in=900)->tuple[str,str]:
    _external_private_key(root,requester_private_key)
    with VaultFS(root) as fs:
        rel=_parts(source.as_posix()); canonical="/".join(rel); data=fs.read("groups",vault_id,*rel); request_id=secrets.token_hex(16)
        actor=_public_identity(requester_public_key)
        body={"version":3,"request_id":request_id,"requester":actor,"requester_principal":_principal(),"vault_id":vault_id,"source":canonical,"sha256":hashlib.sha256(data).hexdigest(),"action":"promote","expires":int(time.time())+expires_in}
        artifact={**body,"request_sig":_signature(_canonical(body),requester_private_key,requester_public_key)}
        fs.write_new((json.dumps(artifact,sort_keys=True)+"\n").encode(),"admin",f"request-{request_id}.json")
        _admin_audit(fs,"promotion-request",vault_id,actor,request_id,canonical)
        return data.decode("utf-8"),request_id

def promotion_sign_request(request_bytes:bytes,requester_public_key:Path,reviewer_private_key:Path,reviewer_public_key:Path)->bytes:
    """Detached reviewer operation: needs request metadata, never the vault."""
    try: artifact=json.loads(request_bytes)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise VaultError("invalid promotion request") from exc
    a=dict(artifact); sig=a.pop("request_sig")
    if a.get("requester")!=_public_identity(requester_public_key): raise VaultError("requester identity does not match key")
    if a.get("requester_principal")!=_public_owner(requester_public_key): raise VaultError("requester public key owner does not match principal")
    _verify(_canonical(a),sig,requester_public_key)
    if a.get("version")!=3 or a["expires"]<time.time(): raise VaultError("invalid or expired promotion request")
    reviewer=_public_identity(reviewer_public_key); reviewer_principal=_principal()
    if reviewer==a["requester"] or reviewer_principal==a.get("requester_principal"): raise VaultError("requester and reviewer must be different authenticated principals")
    approval={"request":artifact,"reviewer":reviewer,"reviewer_principal":reviewer_principal,"approved_at":int(time.time())}
    approval["reviewer_sig"]=_signature(_canonical(approval),reviewer_private_key,reviewer_public_key)
    return (json.dumps(approval,sort_keys=True)+"\n").encode()

def promotion_record_approval(root:Path,approval_bytes:bytes,requester_public_key:Path,reviewer_public_key:Path)->str:
    try: approval=json.loads(approval_bytes)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise VaultError("invalid promotion approval") from exc
    checked=dict(approval); reviewer_sig=checked.pop("reviewer_sig"); request=checked.get("request",{}); request_id=request.get("request_id","")
    if not re.fullmatch(r"[0-9a-f]{32}",request_id): raise VaultError("invalid request id")
    if checked.get("reviewer")!=_public_identity(reviewer_public_key): raise VaultError("reviewer identity does not match key")
    if checked.get("reviewer_principal")!=_public_owner(reviewer_public_key): raise VaultError("reviewer public key owner does not match principal")
    if checked.get("reviewer_principal")==request.get("requester_principal"): raise VaultError("approval lacks independent authenticated principal")
    _verify(_canonical(checked),reviewer_sig,reviewer_public_key)
    a=dict(request); request_sig=a.pop("request_sig")
    if a.get("requester")!=_public_identity(requester_public_key): raise VaultError("requester identity does not match key")
    if a.get("requester_principal")!=_public_owner(requester_public_key): raise VaultError("requester public key owner does not match principal")
    _verify(_canonical(a),request_sig,requester_public_key)
    with VaultFS(root) as fs:
        stored=fs.read("admin",f"request-{request_id}.json")
        if json.loads(stored)!=request: raise VaultError("detached approval does not match stored request")
        if not fs.write_new(approval_bytes,"admin",f"approval-{request_id}.json"): raise VaultError("approval already exists")
        _admin_audit(fs,"promotion-approve",a["vault_id"],f'{checked["reviewer_principal"]}/{checked["reviewer"]}',request_id,a["source"])
    return request_id

def promotion_approve(root:Path,request_id:str,requester_public_key:Path,reviewer_private_key:Path,reviewer_public_key:Path)->str:
    if not re.fullmatch(r"[0-9a-f]{32}",request_id): raise VaultError("invalid request id")
    _external_private_key(root,reviewer_private_key)
    with VaultFS(root) as fs:
        request_bytes=fs.read("admin",f"request-{request_id}.json")
    return promotion_record_approval(root,promotion_sign_request(request_bytes,requester_public_key,reviewer_private_key,reviewer_public_key),requester_public_key,reviewer_public_key)

def promotion_apply(root:Path,vault_id:str,source:Path,review_token:str,requester_public_key:Path,reviewer_public_key:Path)->Path:
    # Validate before opening the root, approval path, or process lock.
    if not isinstance(review_token,str) or not re.fullmatch(r"[0-9a-f]{32}",review_token): raise VaultError("invalid review token")
    with VaultFS(root) as fs:
        lock_dir=fs.dirfd(("admin",))
        try:
            lock_fd=os.open("promotion-apply.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=lock_dir)
            try:
                os.fchmod(lock_fd,0o600); fcntl.flock(lock_fd,fcntl.LOCK_EX)
                if fs.exists("admin",f"used-{review_token}.json"): raise VaultError("promotion approval was already consumed")
                ap=json.loads(fs.read("admin",f"approval-{review_token}.json")); rs=ap.pop("reviewer_sig")
                requester=_public_identity(requester_public_key); reviewer=_public_identity(reviewer_public_key)
                if requester==reviewer or ap.get("reviewer")!=reviewer or ap.get("reviewer_principal")==ap.get("request",{}).get("requester_principal"): raise VaultError("invalid independent approval identity")
                if ap.get("reviewer_principal")!=_public_owner(reviewer_public_key) or ap.get("request",{}).get("requester_principal")!=_public_owner(requester_public_key): raise VaultError("approval public key owner does not match principal")
                _verify(_canonical(ap),rs,reviewer_public_key)
                artifact=ap["request"]; r=dict(artifact); request_sig=r.pop("request_sig")
                if r.get("requester")!=requester: raise VaultError("invalid requester identity")
                _verify(_canonical(r),request_sig,requester_public_key)
                data=fs.read("groups",vault_id,*_parts(source.as_posix()))
                if r["request_id"]!=review_token or r["vault_id"]!=vault_id or r["source"]!="/".join(_parts(source.as_posix())) or r["action"]!="promote" or r["expires"]<time.time() or r["sha256"]!=hashlib.sha256(data).hexdigest(): raise VaultError("approval does not bind current promotion")
                # Exclusive, fsynced reservation is the one-shot commit point. A crash
                # after this line deliberately consumes the token without replay.
                used={"request_id":review_token,"reserved_at":int(time.time()),"state":"consumed"}
                if not fs.write_new(json.dumps(used,sort_keys=True).encode(),"admin",f"used-{review_token}.json"):
                    raise VaultError("promotion approval was already consumed")
                # Build the complete next generation while holding the process lock.
                # Before replace, readers retain the old generation; after replace,
                # readers see the complete new one even if the success audit fails.
                old=fs.read("shared",PROMOTED_FILE) if fs.exists("shared",PROMOTED_FILE) else b""
                if old: _backup(fs,old,PROMOTED_FILE)
                section=f"\n## Explicit promotion from {vault_id}\n\n{data.decode().rstrip()}\n".encode()
                fs.replace(old+section,"shared",PROMOTED_FILE)
                _admin_audit(fs,"promotion-apply",vault_id,f'{ap["reviewer_principal"]}/{ap["reviewer"]}',review_token,r["source"])
            finally: os.close(lock_fd)
        finally: os.close(lock_dir)
    return Path(root).absolute()/"shared"/PROMOTED_FILE
