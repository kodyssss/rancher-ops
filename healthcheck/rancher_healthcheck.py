#!/usr/bin/env python3
"""
rancher_healthcheck.py — Rancher 集群巡检 (完整版)
====================================================
巡检 local 集群 + 所有下游集群的健康状态。

检查项:
  🔍 集群状态      — Active/Ready、K8s版本、Provider 类型
  🔍 控制平面      — etcd/scheduler/controller-manager 健康
  🔍 节点健康      — Ready、资源压力(CPU/Mem/Disk/PID)、版本一致性
  🔍 CNI 巡检      — CNI 类型、DaemonSet 就绪状态
  🔍 CSI 巡检      — StorageClass、CSI 驱动、PVC 状态
  🔍 系统组件      — CoreDNS、Ingress、Metrics-server 健康
  🔍 工作负载      — Deployment/DaemonSet 副本状态、高重启 Pod
  🔍 RBAC 概览     — 各角色用户/组数量
  🔍 事件          — 最近 Warning 事件
  🔍 风险汇总      — 汇总所有不健康的项

用法:
  python3 rancher_healthcheck.py              # 终端报告
  python3 rancher_healthcheck.py -o report.md # 输出 Markdown
  python3 rancher_healthcheck.py -c poc       # 只查某集群
  python3 rancher_healthcheck.py --json       # JSON 格式
  python3 rancher_healthcheck.py --no-deep    # 跳过 k8s API 深度检查

env 文件: 同目录 env.txt
"""

import os, sys, json, re, time, ssl
from collections import defaultdict, Counter

try:
    from urllib.request import Request, urlopen, HTTPError
    from urllib.parse import quote
except ImportError:
    from urllib2 import Request, urlopen, HTTPError
    from urllib import quote

PAGE_SIZE       = 1000
MAX_RETRIES     = 3
RETRY_BACKOFF   = 2.0
REQUEST_TIMEOUT = 60
HTTP_PROXY      = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ── 风险等级 ──
CRITICAL = "CRITICAL"
WARNING  = "WARNING"
INFO     = "INFO"
OK       = "OK"

LEVEL_ICON = {CRITICAL: "🔴", WARNING: "🟡", INFO: "🟢", OK: "✅"}

# ── 已知 CNI DaemonSet 名称模式 ──
CNI_PATTERNS = [
    ("canal",         "Canal (Flannel + Calico)"),
    ("calico-node",   "Calico"),
    ("flannel",       "Flannel"),
    ("cilium",        "Cilium"),
    ("weave-net",     "Weave Net"),
    ("kube-router",   "Kube-router"),
    ("antrea-agent",  "Antrea"),
]

# ── 已知 CSI DaemonSet 名称模式 ──
CSI_PATTERNS = [
    ("longhorn",          "Longhorn"),
    ("csi",               "CSI"),
    ("ebs-csi",           "AWS EBS"),
    ("efs-csi",           "AWS EFS"),
    ("vsphere-csi",       "vSphere"),
    ("nfs-csi",           "NFS"),
    ("rook-ceph",         "Rook Ceph"),
    ("cinder-csi",        "OpenStack Cinder"),
]
CSI_NS = ("kube-system", "longhorn-system", "cattle-system", "rook-ceph",
          "vmware-system-csi")


def load_env(env_path=None):
    if env_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(script_dir, "env.txt"),
                  os.path.join(script_dir, "env"),
                  os.path.join(os.getcwd(), "env.txt")]:
            if os.path.isfile(p):
                env_path = p
                break
    if env_path is None:
        die("找不到 env.txt")
    env_vars = {}
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(?:export\s+)?(\w+)=["\']?(.*?)["\']?\s*$', line)
            if m:
                env_vars[m.group(1)] = m.group(2).rstrip('"\'')
    url = env_vars.get("RANCHER_URL", "").rstrip("/")
    token = env_vars.get("RANCHER_TOKEN", "")
    if not url or not token:
        die("env.txt 缺少 RANCHER_URL / RANCHER_TOKEN")
    return url, token


def die(msg):
    print("ERROR: {}".format(msg), file=sys.stderr)
    sys.exit(1)


def api_get(url, token, path):
    full = url.rstrip("/") + "/" + path.lstrip("/")
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(full)
            req.add_header("Authorization", "Bearer {}".format(token))
            req.add_header("Accept", "application/json")
            if HTTP_PROXY:
                req.set_proxy(HTTP_PROXY, "https")
            resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=SSL_CTX)
            return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code in (404, 403):
                return None
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None
    return None


def api_paginated(url, token, path):
    all_items = []
    page = 0
    marker = None
    sep = "&" if "?" in path else "?"
    while True:
        p = "{}{}limit={}".format(path, sep, PAGE_SIZE)
        if marker:
            p += "&continue={}".format(marker)
        data = api_get(url, token, p)
        if not data:
            break
        items = data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        page += 1
        pagination = data.get("pagination", {})
        if pagination.get("next"):
            marker = pagination["next"]
        elif len(items) < PAGE_SIZE:
            break
        else:
            marker = items[-1].get("id", "")
            if page > 1 and len(all_items) >= PAGE_SIZE * page:
                break
    return all_items


def k8s_api(url, token, cluster_id, path):
    """通过 Rancher proxy 访问下游集群 k8s API"""
    full_path = "k8s/clusters/{}/{}".format(cluster_id, path.lstrip("/"))
    return api_get(url, token, full_path)


def k8s_list(url, token, cluster_id, path):
    """k8s API 分页 (使用 continue token)"""
    items = []
    cont = None
    while True:
        sep = "&" if "?" in path else "?"
        p = path + "{}limit=200".format(sep)
        if cont:
            p += "&continue={}".format(cont)
        data = k8s_api(url, token, cluster_id, p)
        if not data:
            break
        items.extend(data.get("items", []))
        cont = data.get("metadata", {}).get("continue", "")
        if not cont:
            break
    return items


# ═══════════════════════════════════════════
#  基础数据获取
# ═══════════════════════════════════════════

def get_rancher_version(url, token):
    data = api_get(url, token, "v3/settings/server-version")
    return data.get("value", "unknown") if data else "unknown"


def get_clusters(url, token):
    return api_paginated(url, token, "v3/clusters")


def get_nodes_rancher(url, token, cluster_id):
    return api_paginated(url, token, "v3/nodes?clusterId={}".format(cluster_id))


def get_projects(url, token, cluster_id):
    return api_paginated(url, token, "v3/projects?clusterId={}".format(cluster_id))


def get_project_rbac(url, token, project_id):
    p = "v3/projectroletemplatebindings?projectId={}".format(project_id)
    return api_paginated(url, token, p)


def get_cluster_rbac(url, token, cluster_id):
    p = "v3/clusterRoleTemplateBindings?clusterId={}".format(cluster_id)
    return api_paginated(url, token, p)


# ═══════════════════════════════════════════
#  检查逻辑
# ═══════════════════════════════════════════

def check_control_plane(url, token, cluster_id, cname, deep):
    """检查控制平面: etcd, apiserver, scheduler, controller-manager + 数量一致性"""
    result = {"status": OK, "components": {}, "pod_counts": {}, "issues": []}
    if not deep:
        return result

    # 控制平面 pod 名称前缀
    CTRL_PODS = {
        "etcd":                     "etcd",
        "kube-apiserver":           "apiserver",
        "kube-controller-manager":  "controller-manager",
        "kube-scheduler":           "scheduler",
    }

    pods = k8s_list(url, token, cluster_id, "api/v1/pods?fieldSelector=spec.nodeName!=")
    ctrl_pods = defaultdict(lambda: {"ready": 0, "total": 0, "restarts": 0, "count": 0})

    for p in pods:
        ns = p["metadata"]["namespace"]
        name = p["metadata"]["name"]
        if ns != "kube-system":
            continue
        comp = None
        for prefix, label in CTRL_PODS.items():
            if name.startswith(prefix + "-"):
                comp = label
                break
        if not comp and name.startswith("cloud-controller-manager-"):
            comp = "cloud-controller-manager"
        if not comp:
            continue
        csts = p["status"].get("containerStatuses", [])
        ctrl_pods[comp]["ready"] += sum(1 for c in csts if c.get("ready"))
        ctrl_pods[comp]["total"] += len(csts)
        ctrl_pods[comp]["restarts"] += sum(c.get("restartCount", 0) for c in csts)
        ctrl_pods[comp]["count"] += 1

    if not ctrl_pods:
        result["components"]["control-plane"] = "No pods found"
        result["issues"].append({
            "level": CRITICAL,
            "item": "ControlPlane Missing",
            "detail": "{}: 未发现控制平面 pod".format(cname),
        })
        result["status"] = CRITICAL
        return result

    # 检测期望数量（HA=3, 单节点=1）
    counts = [st["count"] for st in ctrl_pods.values()]
    expected = 3 if any(c > 1 for c in counts) else 1

    for comp in sorted(ctrl_pods):
        st = ctrl_pods[comp]
        cnt = st["count"]
        healthy = st["ready"] == st["total"] and st["total"] > 0
        status_text = "{} pod".format(cnt)
        if cnt != expected:
            status_text += " (期望 {})".format(expected)

        result["components"][comp] = "{} {}".format(
            "✅" if healthy else "⚠️", status_text)
        result["pod_counts"][comp] = {
            "count": cnt, "expected": expected,
            "ready": st["ready"], "total": st["total"]
        }

        if not healthy:
            result["issues"].append({
                "level": CRITICAL,
                "item": "ControlPlane {} Down".format(comp),
                "detail": "{}: {}/{} ready, count={}".format(
                    cname, st["ready"], st["total"], cnt),
            })
        elif cnt != expected:
            result["issues"].append({
                "level": WARNING,
                "item": "ControlPlane {} Count".format(comp),
                "detail": "{}: {} pods (期望 {})".format(cname, cnt, expected),
            })
        if st["restarts"] > 10:
            result["issues"].append({
                "level": WARNING,
                "item": "ControlPlane {} Restarts".format(comp),
                "detail": "{}: {} restarts".format(cname, st["restarts"]),
            })

    # 检查缺失的组件
    expected_comps = {"etcd", "apiserver", "controller-manager", "scheduler"}
    missing = expected_comps - set(ctrl_pods.keys())
    for m in missing:
        result["issues"].append({
            "level": CRITICAL,
            "item": "ControlPlane {} Missing".format(m),
            "detail": "{}: 未发现 {} pod".format(cname, m),
        })

    result["expected_count"] = expected
    result["ha"] = expected == 3

    has_issue = any(i["level"] in (CRITICAL, WARNING) for i in result["issues"])
    result["status"] = CRITICAL if any(i["level"] == CRITICAL for i in result["issues"]) else (
        WARNING if has_issue else OK)

    return result


def check_nodes(url, token, cluster_id, cname, deep, k8s_version=""):
    """检查节点: Rancher + k8s API + kubelet 版本对比"""
    result = {"nodes": [], "issues": [], "kubelet_versions": set(),
              "kernel_versions": set(), "container_runtimes": set()}

    # Rancher 节点数据
    rn = get_nodes_rancher(url, token, cluster_id)

    # k8s 节点数据 (deep)
    k8s_nodes = []
    if deep:
        k8s_data = k8s_api(url, token, cluster_id, "api/v1/nodes")
        if k8s_data:
            k8s_nodes = k8s_data.get("items", [])

    # 合并
    k8s_map = {}
    for kn in k8s_nodes:
        k8s_map[kn["metadata"]["name"]] = kn

    for n in rn:
        nid   = n["id"]
        host  = n.get("hostname", n.get("nodeName", nid.split(":")[-1]))
        state = n.get("state", "?")
        info  = n.get("info", {})
        cpu   = info.get("cpu", {})
        mem   = info.get("memory", {})
        os_i  = info.get("os", {})
        k8s_i = info.get("kubernetes", {})
        alloc = n.get("allocatable", {})
        capa  = n.get("capacity", {})

        kubelet_ver = k8s_i.get("kubeletVersion", "?") if isinstance(k8s_i, dict) else "?"
        kernel_ver  = os_i.get("kernelVersion", "?") if isinstance(os_i, dict) else "?"
        container_runtime = os_i.get("dockerVersion", "?") if isinstance(os_i, dict) else "?"

        if kubelet_ver and kubelet_ver != "?":
            result["kubelet_versions"].add(kubelet_ver)
        if kernel_ver and kernel_ver != "?":
            result["kernel_versions"].add(kernel_ver)
        if container_runtime and container_runtime != "?":
            result["container_runtimes"].add(container_runtime)

        node_issues = []

        # ← Rancher conditions
        for cond in n.get("conditions", []):
            ct = cond.get("type", "")
            cs = cond.get("status", "Unknown")
            if ct == "Ready" and cs != "True":
                node_issues.append({
                    "level": CRITICAL,
                    "item": "Node NotReady",
                    "detail": "{} ({})".format(host, cname),
                })
            elif ct in ("MemoryPressure", "DiskPressure", "PIDPressure") and cs == "True":
                node_issues.append({
                    "level": WARNING,
                    "item": "Node {}".format(ct),
                    "detail": "{} ({})".format(host, cname),
                })

        if state not in ("active",):
            node_issues.append({
                "level": WARNING,
                "item": "Node State",
                "detail": "{} state={} ({})".format(host, state, cname),
            })

        # ← k8s node conditions
        kn = k8s_map.get(host, k8s_map.get(nid.split(":")[-1], {}))
        k8s_conds = []
        if kn:
            for cond in kn.get("status", {}).get("conditions", []):
                ct = cond["type"]
                cs = cond["status"]
                k8s_conds.append("{}={}".format(ct, cs))
                if ct == "Ready" and cs != "True":
                    node_issues.append({
                        "level": CRITICAL,
                        "item": "Node K8s NotReady",
                        "detail": "{} reason={} ({})".format(
                            host, cond.get("reason", "?"), cname),
                    })
                elif ct in ("MemoryPressure", "DiskPressure", "PIDPressure") and cs == "True":
                    node_issues.append({
                        "level": WARNING,
                        "item": "Node K8s {}".format(ct),
                        "detail": "{} ({})".format(host, cname),
                    })
                elif ct == "NetworkUnavailable" and cs == "True":
                    node_issues.append({
                        "level": CRITICAL,
                        "item": "Node NetworkUnavailable",
                        "detail": "{} CNI 可能异常 ({})".format(host, cname),
                    })

        # Kubelet 版本偏移检查
        kubelet_skew = False
        if k8s_version and kubelet_ver and kubelet_ver != "?" and k8s_version != "?":
            try:
                kv_parts = kubelet_ver.replace("v","").split(".")
                cv_parts = k8s_version.replace("v","").split(".")
                if len(kv_parts) >= 2 and len(cv_parts) >= 2:
                    if kv_parts[0] != cv_parts[0] or kv_parts[1] != cv_parts[1]:
                        kubelet_skew = True
                        node_issues.append({
                            "level": WARNING,
                            "item": "Kubelet Version Skew",
                            "detail": "{}: kubelet={} vs cluster={}".format(
                                host, kubelet_ver, k8s_version),
                        })
            except:
                pass

        node_info = {
            "hostname": host,
            "state": state,
            "cpu_cores": cpu.get("count", "?"),
            "memory_kib": str(mem.get("memTotalKiB", "?")),
            "kubelet": kubelet_ver,
            "kernel": kernel_ver,
            "runtime": container_runtime,
            "alloc_cpu": alloc.get("cpu", "?"),
            "alloc_mem": alloc.get("memory", "?"),
            "alloc_pods": alloc.get("pods", "?"),
            "capacity_pods": capa.get("pods", "?"),
            "ready": "Ready" in [c.get("type") for c in n.get("conditions", [])
                                 if c.get("status") == "True" and c.get("type") == "Ready"],
            "k8s_conditions": k8s_conds,
            "kubelet_skew": kubelet_skew,
        }

        result["nodes"].append(node_info)
        result["issues"].extend(node_issues)

    # 版本一致性
    if len(result["kubelet_versions"]) > 1:
        result["issues"].append({
            "level": WARNING,
            "item": "Kubelet Version Mismatch",
            "detail": "{}: {}".format(cname, ", ".join(sorted(result["kubelet_versions"]))),
        })

    return result


def check_non_running_pods(url, token, cluster_id, cname, deep):
    """检查非 Running 状态的 Pod（全命名空间）"""
    result = {"pods": [], "issues": [], "status": OK}
    if not deep:
        return result

    pods = k8s_list(url, token, cluster_id, "api/v1/pods")
    for p in pods:
        ns   = p["metadata"]["namespace"]
        name = p["metadata"]["name"]
        phase = p["status"].get("phase", "?")

        if phase in ("Running", "Succeeded"):
            continue

        # 收集容器等待原因
        csts = p["status"].get("containerStatuses", [])
        wait_reasons = []
        crash_count = 0
        for c in csts:
            wait = c.get("state", {}).get("waiting", {})
            if wait:
                wr = wait.get("reason", "")
                if wr:
                    wait_reasons.append(wr)
            term = c.get("state", {}).get("terminated", {})
            if term:
                crash_count += term.get("restartCount", 0)
                tr = term.get("reason", "")
                if tr and tr not in wait_reasons:
                    wait_reasons.append(tr)

        reason_str = ",".join(wait_reasons) if wait_reasons else phase
        pod_info = {
            "namespace": ns, "name": name, "phase": phase,
            "reason": reason_str,
            "restarts": crash_count,
        }
        result["pods"].append(pod_info)

        # 根据严重程度分级
        if "CrashLoopBackOff" in reason_str or "Error" in reason_str:
            level = WARNING
        elif "ImagePullBackOff" in reason_str or "ErrImagePull" in reason_str:
            level = WARNING
        elif "Pending" == phase and not wait_reasons:
            level = INFO
        else:
            level = WARNING

        result["issues"].append({
            "level": level,
            "item": "Pod {}".format(phase),
            "detail": "{}/{}/{}: phase={} reason={}".format(
                cname, ns, name, phase, reason_str),
        })

    if result["pods"]:
        result["status"] = WARNING if any(
            i["level"] in (CRITICAL, WARNING) for i in result["issues"]) else INFO

    return result


def check_rancher_health(url, token, cluster_id, cname, deep):
    """检查 Rancher 管理组件: cattle-system, fleet, capi"""
    result = {"components": {}, "issues": [], "status": OK}
    if not deep:
        return result

    # Rancher 管理命名空间
    RANCHER_NS = {"cattle-system", "cattle-fleet-local-system",
                  "cattle-provisioning-capi-system"}

    pods = k8s_list(url, token, cluster_id, "api/v1/pods")

    for p in pods:
        ns = p["metadata"]["namespace"]
        if ns not in RANCHER_NS:
            continue
        name = p["metadata"]["name"]
        phase = p["status"].get("phase", "?")
        csts = p["status"].get("containerStatuses", [])
        ready = sum(1 for c in csts if c.get("ready"))
        total = len(csts)
        restarts = sum(c.get("restartCount", 0) for c in csts)

        healthy = phase == "Running" and ready == total and total > 0
        key = "{}/{}".format(ns, name)
        result["components"][key] = {
            "ready": ready, "total": total,
            "phase": phase, "restarts": restarts, "healthy": healthy,
        }

        if not healthy:
            result["issues"].append({
                "level": WARNING,
                "item": "Rancher Pod {}".format(phase),
                "detail": "{}: {}/{} {}/{}({}) ready".format(
                    cname, ns, name, ready, total, phase),
            })
        if restarts > 5:
            result["issues"].append({
                "level": INFO,
                "item": "Rancher Pod Restarts",
                "detail": "{}: {}/{} restarts={}".format(
                    cname, ns, name, restarts),
            })

    if any(i["level"] in (CRITICAL, WARNING) for i in result["issues"]):
        result["status"] = WARNING

    return result


def check_cni(url, token, cluster_id, cname, deep):
    """检查 CNI: DaemonSet 状态、所有节点覆盖"""
    result = {"cni_type": "unknown", "status": OK, "daemonsets": [], "issues": []}
    if not deep:
        return result

    pods = k8s_list(url, token, cluster_id, "api/v1/pods")
    ds_data = k8s_api(url, token, cluster_id, "apis/apps/v1/daemonsets")
    ds_list = ds_data.get("items", []) if ds_data else []

    # 通过 DaemonSet 识别 CNI
    cni_ds = []
    for ds in ds_list:
        name = ds["metadata"]["name"].lower()
        ns   = ds["metadata"]["namespace"]
        for pattern, label in CNI_PATTERNS:
            if pattern in name:
                ready = ds["status"].get("numberReady", 0) or 0
                desired = ds["status"].get("desiredNumberScheduled", 0) or 0
                cni_ds.append({
                    "name": name, "namespace": ns, "label": label,
                    "ready": ready, "desired": desired,
                    "healthy": ready == desired and desired > 0,
                })
                if not cni_ds[-1]["healthy"]:
                    result["issues"].append({
                        "level": WARNING,
                        "item": "CNI NotReady",
                        "detail": "{}/{}/{}: {}/{} ready ({})".format(
                            cname, ns, name, ready, desired, label),
                    })
                break

    if cni_ds:
        result["cni_type"] = ", ".join(d["label"] for d in cni_ds)
    else:
        # 兜底：从 pod 和 kubeconfig 推测
        for p in pods:
            for pattern, label in CNI_PATTERNS:
                if pattern in p["metadata"]["name"].lower():
                    result["cni_type"] = label
                    break
            if result["cni_type"] != "unknown":
                break

    if result["cni_type"] == "unknown":
        result["issues"].append({
            "level": INFO,
            "item": "CNI Unknown",
            "detail": "{}: 未检测到已知 CNI 类型".format(cname),
        })

    result["daemonsets"] = cni_ds
    return result


def check_csi(url, token, cluster_id, cname, deep):
    """检查 CSI: StorageClass、CSI DaemonSet、PVC 状态"""
    result = {"drivers": [], "storage_classes": [], "pvc_total": 0,
              "pvc_pending": 0, "status": OK, "issues": []}
    if not deep:
        return result

    # StorageClass
    sc_data = k8s_api(url, token, cluster_id, "apis/storage.k8s.io/v1/storageclasses")
    if sc_data:
        for sc in sc_data.get("items", []):
            sc_name = sc["metadata"]["name"]
            provisioner = sc.get("provisioner", "?")
            is_default = sc["metadata"].get("annotations", {}).get(
                "storageclass.kubernetes.io/is-default-class", "false")
            result["storage_classes"].append({
                "name": sc_name, "provisioner": provisioner,
                "default": is_default == "true",
            })

    # CSI DaemonSet / Deployment
    ds_data = k8s_api(url, token, cluster_id, "apis/apps/v1/daemonsets")
    ds_list = ds_data.get("items", []) if ds_data else []
    deploys = k8s_api(url, token, cluster_id, "apis/apps/v1/deployments")
    deploy_list = deploys.get("items", []) if deploys else []

    for obj in ds_list + deploy_list:
        name = obj["metadata"]["name"].lower()
        ns   = obj["metadata"]["namespace"]
        if ns not in CSI_NS:
            continue
        for pattern, label in CSI_PATTERNS:
            if pattern in name:
                ready = obj["status"].get("numberReady", 0) or 0
                if ready == 0:
                    ready = obj["status"].get("readyReplicas", 0) or 0
                desired = obj["status"].get("desiredNumberScheduled", 0) or 0
                if desired == 0:
                    desired = obj["status"].get("replicas", 0) or 0
                kind = "DaemonSet" if "DaemonSet" in str(type(obj)) else "Deployment"
                result["drivers"].append({
                    "name": name, "namespace": ns, "label": label,
                    "kind": kind, "ready": ready, "desired": desired,
                    "healthy": ready == desired and desired > 0,
                })
                if not result["drivers"][-1]["healthy"] and ready > 0:
                    result["issues"].append({
                        "level": WARNING,
                        "item": "CSI NotReady",
                        "detail": "{}/{}/{}: {}/{} ready ({})".format(
                            cname, ns, name, ready, desired, label),
                    })

    # PVC 状态
    pvcs = k8s_list(url, token, cluster_id, "api/v1/persistentvolumeclaims")
    result["pvc_total"] = len(pvcs)
    for pvc in pvcs:
        phase = pvc["status"].get("phase", "Unknown")
        if phase != "Bound":
            result["pvc_pending"] += 1
            result["issues"].append({
                "level": WARNING,
                "item": "PVC Not Bound",
                "detail": "{}/{}/{}: phase={}".format(
                    cname, pvc["metadata"]["namespace"],
                    pvc["metadata"]["name"], phase),
            })

    return result


def check_system_components(url, token, cluster_id, cname, deep):
    """检查系统组件: CoreDNS, Ingress, Metrics-server, 高重启 Pod"""
    result = {"components": [], "high_restarts": [], "issues": [],
              "failed_pods": [], "status": OK}
    if not deep:
        return result

    KEY_COMPONENTS = {
        "coredns":       "CoreDNS",
        "rke2-coredns":  "CoreDNS",
        "kube-dns":      "CoreDNS",
        "ingress-nginx": "Ingress NGINX",
        "metrics-server": "Metrics Server",
        "rke2-ingress-nginx": "Ingress NGINX",
        "rke2-metrics-server": "Metrics Server",
    }

    pods = k8s_list(url, token, cluster_id, "api/v1/pods")
    ds_data = k8s_api(url, token, cluster_id, "apis/apps/v1/daemonsets")
    ds_list = ds_data.get("items", []) if ds_data else []
    deploys = k8s_api(url, token, cluster_id, "apis/apps/v1/deployments")
    deploy_list = deploys.get("items", []) if deploys else []

    # 检查关键组件 DaemonSet/Deployment
    for obj in ds_list + deploy_list:
        name = obj["metadata"]["name"]
        ns   = obj["metadata"]["namespace"]
        if ns != "kube-system":
            continue
        for key, label in KEY_COMPONENTS.items():
            if (key in name.lower() or name.lower().startswith(key.lower())):
                # 跳过 autoscaler 类 deployment
                if "autoscaler" in name.lower():
                    continue
                ready = obj["status"].get("numberReady", 0) or 0
                if ready == 0:
                    ready = obj["status"].get("readyReplicas", 0) or 0
                desired = obj["status"].get("desiredNumberScheduled", 0) or 0
                if desired == 0:
                    desired = obj["status"].get("replicas", 0) or 0
                result["components"].append({
                    "name": name, "label": label, "ready": ready, "desired": desired,
                    "healthy": ready == desired and desired > 0,
                })
                if not result["components"][-1]["healthy"]:
                    result["issues"].append({
                        "level": WARNING,
                        "item": "{} NotReady".format(label),
                        "detail": "{}: {}/{} ready".format(cname, ready, desired),
                    })
                break

    # 高重启 + 异常 pod
    for p in pods:
        ns   = p["metadata"]["namespace"]
        name = p["metadata"]["name"]
        phase = p["status"].get("phase", "?")
        csts = p["status"].get("containerStatuses", [])
        ready = sum(1 for c in csts if c.get("ready"))
        total = len(csts)
        restarts = sum(c.get("restartCount", 0) for c in csts)

        # helm-install jobs (Completed 的正常)
        if "helm-install" in name and phase in ("Succeeded",):
            continue

        if restarts > 20:
            result["high_restarts"].append({
                "namespace": ns, "name": name, "restarts": restarts, "phase": phase,
            })
            result["issues"].append({
                "level": WARNING,
                "item": "High Restarts",
                "detail": "{}/{}/{}: {} restarts".format(cname, ns, name, restarts),
            })

        if phase in ("Failed", "Unknown"):
            if "helm-install" in name:
                continue  # helm-install failed 也可能正常
            result["failed_pods"].append({
                "namespace": ns, "name": name, "phase": phase,
            })
            result["issues"].append({
                "level": WARNING,
                "item": "Pod Failed",
                "detail": "{}/{}/{}: phase={}".format(cname, ns, name, phase),
            })

    return result


def check_workloads(url, token, cluster_id, cname, deep):
    """检查工作负载: Deployment/DaemonSet 副本状态"""
    result = {"mismatched": [], "issues": [], "status": OK}
    if not deep:
        return result

    for kind, api_path in [("Deployment", "apis/apps/v1/deployments"),
                            ("DaemonSet", "apis/apps/v1/daemonsets"),
                            ("StatefulSet", "apis/apps/v1/statefulsets")]:
        data = k8s_api(url, token, cluster_id, api_path)
        if not data:
            continue
        for obj in data.get("items", []):
            name = obj["metadata"]["name"]
            ns   = obj["metadata"]["namespace"]
            if ns in ("kube-system", "cattle-system", "longhorn-system",
                      "calico-system", "kube-public"):
                continue  # 系统组件在 check_system_components 中处理

            if kind == "DaemonSet":
                ready = obj["status"].get("numberReady", 0) or 0
                desired = obj["status"].get("desiredNumberScheduled", 0) or 0
            else:
                ready = obj["status"].get("readyReplicas", 0) or 0
                desired = obj.get("spec", {}).get("replicas", 0) or 0

            if ready != desired and desired > 0:
                result["mismatched"].append({
                    "namespace": ns, "name": name, "kind": kind,
                    "ready": ready, "desired": desired,
                })

    if result["mismatched"]:
        result["status"] = WARNING
        for m in result["mismatched"][:10]:
            result["issues"].append({
                "level": WARNING,
                "item": "{} Replicas".format(m["kind"]),
                "detail": "{}/{}/{}: {}/{} replicas ({})".format(
                    cname, m["namespace"], m["name"],
                    m["ready"], m["desired"], cname),
            })

    return result


def check_events(url, token, cluster_id, cname, deep):
    """检查最近 Warning 事件，特别关注控制平面/etcd"""
    result = {"warnings": [], "ctrl_warnings": [], "issues": [], "status": OK}
    if not deep:
        return result

    CTRL_KEYWORDS = {"etcd", "apiserver", "kube-apiserver", "scheduler",
                     "controller-manager", "kube-controller", "cloud-controller"}

    data = k8s_api(url, token, cluster_id, "api/v1/events?limit=200")
    if not data:
        return result

    for e in data.get("items", []):
        if e.get("type") != "Warning":
            continue
        reason = e.get("reason", "?")
        msg = (e.get("message", "") or "")[:120]
        ns = e["metadata"]["namespace"]
        name = e["metadata"]["name"]
        evt = {
            "namespace": ns,
            "name": name,
            "reason": reason,
            "message": msg,
            "time": e.get("lastTimestamp", e.get("eventTime",
                    e["metadata"].get("creationTimestamp", ""))),
        }

        # 控制平面/etcd 相关事件单独追踪
        is_ctrl = any(kw in (name + msg + reason).lower() for kw in CTRL_KEYWORDS)
        if is_ctrl:
            result["ctrl_warnings"].append(evt)
        else:
            result["warnings"].append(evt)

    # 控制平面事件优先展示
    if result["ctrl_warnings"]:
        result["status"] = WARNING
        for w in result["ctrl_warnings"][:10]:
            result["issues"].append({
                "level": WARNING,
                "item": "Ctrl Event: {}".format(w["reason"]),
                "detail": "{} ({})".format(w["message"][:100], cname),
            })

    if result["warnings"]:
        if result["status"] != WARNING:
            result["status"] = INFO
        seen = set()
        uniq = []
        for w in result["warnings"]:
            key = w["reason"]
            if key not in seen:
                seen.add(key)
                uniq.append(w)
                if len(uniq) >= 10:
                    break
        result["warnings"] = uniq
        for w in uniq:
            result["issues"].append({
                "level": INFO,
                "item": "Event: {}".format(w["reason"]),
                "detail": "{} ({}/{})".format(w["message"][:80], cname, w["namespace"]),
            })

    return result


def check_projects_and_rbac(url, token, cluster_id, cname):
    """检查项目和 RBAC"""
    result = {"projects": [], "rbac_cluster": {}, "issues": [], "status": OK}

    projects = get_projects(url, token, cluster_id)
    result["project_count"] = len(projects)

    for proj in projects:
        pid   = proj["id"]
        pname = proj.get("name", pid)
        pstate = proj.get("state", "?")
        bindings = get_project_rbac(url, token, pid)
        member_count = len([b for b in bindings
                          if b.get("userId") or b.get("userPrincipalId")
                          or b.get("groupPrincipalId")])

        proj_info = {
            "name": pname, "state": pstate,
            "member_count": member_count,
            "system": pname in ("System", "Default"),
        }
        result["projects"].append(proj_info)

        if pstate not in ("active",):
            result["issues"].append({
                "level": WARNING,
                "item": "Project State",
                "detail": "{}/{} state={}".format(cname, pname, pstate),
            })

        if member_count == 0 and not proj_info["system"]:
            result["issues"].append({
                "level": INFO,
                "item": "Empty Project",
                "detail": "{}/{} 无成员绑定".format(cname, pname),
            })

    # 集群级 RBAC
    crtb = get_cluster_rbac(url, token, cluster_id)
    cluster_roles = Counter()
    for b in crtb:
        rt = b.get("roleTemplateId", "")
        if rt:
            cluster_roles[rt] += 1
    result["rbac_cluster"] = dict(cluster_roles)

    if not crtb:
        result["issues"].append({
            "level": INFO,
            "item": "No Cluster Admins",
            "detail": "集群 {} 无集群级角色绑定".format(cname),
        })

    return result


def check_cluster(url, token, cluster, deep):
    """检查单个集群"""
    cid   = cluster["id"]
    cname = cluster.get("name", cid)
    state = cluster.get("state", "unknown")
    is_local = cid == "local"
    provider = cluster.get("provider", cluster.get("driver", "?"))
    ver = cluster.get("version", {})
    k8s_ver = ver.get("gitVersion", cluster.get("rke2Config", {}).get("kubernetesVersion", "?"))

    result = {
        "id": cid, "name": cname, "local": is_local,
        "state": state, "provider": provider, "k8s_version": k8s_ver,
        "conditions": {},
    }

    # 集群条件
    for c in cluster.get("conditions", []):
        result["conditions"][c.get("type", "?")] = c.get("status", "Unknown")

    # 汇总 issues
    all_issues = []

    # State
    if state not in ("active",):
        all_issues.append({
            "level": CRITICAL,
            "item": "Cluster State",
            "detail": "集群 {} state={}".format(cname, state),
        })
    if result["conditions"].get("Ready") != "True":
        all_issues.append({
            "level": CRITICAL,
            "item": "Cluster NotReady",
            "detail": "集群 {} Ready={}".format(cname, result["conditions"].get("Ready", "?")),
        })

    # 控制平面
    cp = check_control_plane(url, token, cid, cname, deep)
    result["control_plane"] = cp
    all_issues.extend(cp["issues"])

    # 节点
    nd = check_nodes(url, token, cid, cname, deep, k8s_ver)
    result["nodes"] = nd["nodes"]
    result["node_issues"] = nd["issues"]
    result["kubelet_versions"] = list(nd["kubelet_versions"])
    result["kernel_versions"] = list(nd["kernel_versions"])
    result["container_runtimes"] = list(nd["container_runtimes"])
    all_issues.extend(nd["issues"])

    # CNI
    cni = check_cni(url, token, cid, cname, deep)
    result["cni"] = cni
    all_issues.extend(cni["issues"])

    # CSI
    csi = check_csi(url, token, cid, cname, deep)
    result["csi"] = csi
    all_issues.extend(csi["issues"])

    # 系统组件
    sysc = check_system_components(url, token, cid, cname, deep)
    result["system_components"] = sysc
    all_issues.extend(sysc["issues"])

    # 非 Running Pod
    nrp = check_non_running_pods(url, token, cid, cname, deep)
    result["non_running_pods"] = nrp
    all_issues.extend(nrp["issues"])

    # Rancher 管理组件
    rh = check_rancher_health(url, token, cid, cname, deep)
    result["rancher_health"] = rh
    all_issues.extend(rh["issues"])

    # 工作负载
    wl = check_workloads(url, token, cid, cname, deep)
    result["workloads"] = wl
    all_issues.extend(wl["issues"])

    # 事件
    ev = check_events(url, token, cid, cname, deep)
    result["events"] = ev
    all_issues.extend(ev["issues"])

    # 项目 + RBAC
    pr = check_projects_and_rbac(url, token, cid, cname)
    result["projects"] = pr["projects"]
    result["project_count"] = pr["project_count"]
    result["rbac"] = pr
    all_issues.extend(pr["issues"])

    result["issues"] = all_issues
    result["deep_check"] = deep

    # 整体评级
    has_critical = any(i["level"] == CRITICAL for i in all_issues)
    has_warning  = any(i["level"] == WARNING for i in all_issues)
    result["health"] = CRITICAL if has_critical else WARNING if has_warning else OK

    return result


# ═══════════════════════════════════════════
#  报告生成
# ═══════════════════════════════════════════

def _hdr(lines, title, level=2):
    lines.append("{} {}".format("#" * level, title))
    lines.append("")


def _table(lines, headers, rows):
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["------"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    lines.append("")


def _icon(level):
    return LEVEL_ICON.get(level, "⬜")


def _mem_fmt(kib_str):
    """格式化内存: 3843688Ki → 3.7 GiB"""
    try:
        val = int(re.sub(r"[^0-9]", "", str(kib_str)))
        if val == 0:
            return kib_str
        if val > 1024 * 1024:
            return "{:.1f} GiB".format(val / 1024.0 / 1024.0)
        return "{:.1f} MiB".format(val / 1024.0)
    except:
        return str(kib_str)


def generate_report(results, version, url, deep):
    """生成 Markdown 巡检报告"""
    lines = []
    total_issues = sum(len(r["issues"]) for r in results)
    critical = sum(1 for r in results for i in r["issues"] if i["level"] == CRITICAL)
    warnings = sum(1 for r in results for i in r["issues"] if i["level"] == WARNING)
    infos    = sum(1 for r in results for i in r["issues"] if i["level"] == INFO)

    total_nodes     = sum(len(r["nodes"]) for r in results)
    total_projects  = sum(r.get("project_count", 0) for r in results)
    total_clusters  = len(results)

    _hdr(lines, "Rancher 集群巡检报告", 2)
    _table(lines, ["项目", "值"], [
        ["Rancher URL", url],
        ["Rancher 版本", version],
        ["巡检时间", time.strftime("%Y-%m-%d %H:%M:%S")],
        ["深度检查 (k8s API)", "是" if deep else "否"],
        ["集群总数", total_clusters],
        ["节点总数", total_nodes],
        ["项目总数", total_projects],
    ])

    # ── 风险摘要 ──
    _hdr(lines, "⚠️ 风险摘要", 3)
    if total_issues == 0:
        lines.append("> ✅ 未发现风险项，所有集群运行正常")
        lines.append("")
    else:
        lines.append("| 等级 | 数量 |")
        lines.append("|------|------|")
        if critical:
            lines.append("| {} {} | {} |".format(_icon(CRITICAL), CRITICAL, critical))
        if warnings:
            lines.append("| {} {} | {} |".format(_icon(WARNING), WARNING, warnings))
        if infos:
            lines.append("| {} {} | {} |".format(_icon(INFO), INFO, infos))
        lines.append("")

    # ── 集群概览 ──
    _hdr(lines, "🖥️ 集群概览", 3)
    _table(lines, ["集群", "类型", "Provider", "K8s", "状态", "节点", "项目", "健康"],
           [["{} {}".format(_icon(r["health"]), r["name"]),
             "local" if r["local"] else "downstream",
             r["provider"],
             r["k8s_version"][:25],
             r["state"],
             len(r["nodes"]),
             r.get("project_count", 0),
             "{} {}".format(_icon(r["health"]), r["health"])]
            for r in results])

    # ── 逐集群详情 ──
    for r in results:
        if r["issues"]:
            _hdr(lines, "{} {} — 风险详情".format(_icon(r["health"]), r["name"]), 3)
            for issue in r["issues"]:
                lines.append("- {} **{}**: {}".format(
                    _icon(issue["level"]), issue["item"], issue["detail"]))
            lines.append("")

    for r in results:
        _hdr(lines, "🖧 {} — 节点详情".format(r["name"]), 3)
        if r["nodes"]:
            rows = []
            for n in r["nodes"]:
                ready_icon = "✅" if n["ready"] else "🔴"
                rows.append([
                    "{} {}".format(ready_icon, n["hostname"]),
                    n["state"],
                    "{}核".format(n["cpu_cores"]),
                    _mem_fmt(n["memory_kib"]),
                    "{}/{}".format(n["alloc_pods"], n["capacity_pods"]),
                    n["kubelet"][:25],
                    n["runtime"][:25] if n["runtime"] else "?",
                ])
            _table(lines, ["节点", "状态", "CPU", "内存", "Pods", "Kubelet", "Runtime"], rows)
        else:
            lines.append("_(无节点数据)_\n")

        # 版本一致性
        if len(r.get("kubelet_versions", [])) > 1:
            lines.append("⚠️ **Kubelet 版本不一致**: {}".format(
                ", ".join(r["kubelet_versions"])))
            lines.append("")
        if len(r.get("container_runtimes", [])) > 1:
            lines.append("⚠️ **容器运行时不一致**: {}".format(
                ", ".join(r["container_runtimes"])))
            lines.append("")

        # ── Kubelet 状态 ──
        if r["nodes"]:
            kubelet_issues = []
            for n in r["nodes"]:
                if not n["ready"]:
                    kubelet_issues.append("🔴 {}: NotReady".format(n["hostname"]))
                if n.get("kubelet_skew"):
                    kubelet_issues.append("🟡 {}: kubelet={} vs cluster={}".format(
                        n["hostname"], n["kubelet"][:25], r["k8s_version"][:25]))
            if kubelet_issues:
                for ki in kubelet_issues:
                    lines.append("- {}".format(ki))
                lines.append("")
            elif r["nodes"]:
                # 所有节点 kubelet 正常
                versions = r.get("kubelet_versions", [])
                if len(versions) == 1:
                    lines.append("✅ Kubelet 状态正常 ({} 节点, 版本 {})".format(
                        len(r["nodes"]), list(versions)[0][:25]))
                    lines.append("")

        # ── 控制平面 ──
        cp = r.get("control_plane", {})
        if cp.get("components"):
            ha_label = " (HA x3)" if cp.get("ha") else " (单节点)"
            _hdr(lines, "⚙️ {} — 控制平面{}".format(r["name"], ha_label), 3)
            rows = []
            for comp, st in cp["components"].items():
                # st 现在是 "✅ X pod" 或 "⚠️ X/Y ready, Z pod"
                # 直接展示，已经包含状态图标
                rows.append([comp, st])
            _table(lines, ["组件", "状态"], rows)

        # ── CNI ──
        cni = r.get("cni", {})
        if cni.get("cni_type"):
            _hdr(lines, "🌐 {} — CNI: {}".format(r["name"], cni["cni_type"]), 3)
            if cni.get("daemonsets"):
                _table(lines, ["DaemonSet", "Ready", "Desired", "状态"],
                       [["{}/{}".format(d["namespace"], d["name"]),
                         d["ready"], d["desired"],
                         "✅" if d["healthy"] else "⚠️"]
                        for d in cni["daemonsets"]])

        # ── CSI ──
        csi = r.get("csi", {})
        has_csi_info = csi.get("drivers") or csi.get("storage_classes") or csi.get("pvc_total", 0) > 0
        if deep and has_csi_info:
            _hdr(lines, "💾 {} — 存储 (CSI)".format(r["name"]), 3)
            if csi.get("storage_classes"):
                _table(lines, ["StorageClass", "Provisioner", "默认"],
                       [["⭐ " + sc["name"] if sc["default"] else sc["name"],
                         sc["provisioner"],
                         "是" if sc["default"] else ""]
                        for sc in csi["storage_classes"]])
            if csi.get("drivers"):
                _table(lines, ["CSI Driver", "Kind", "Ready", "Desired", "状态"],
                       [["{}/{}".format(d["namespace"], d["name"]),
                         d["kind"], d["ready"], d["desired"],
                         "✅" if d["healthy"] else "⚠️"]
                        for d in csi["drivers"]])
            if csi.get("pvc_pending", 0) > 0:
                lines.append("⚠️ {} 个 PVC 未 Bound (共 {} 个)".format(
                    csi["pvc_pending"], csi["pvc_total"]))
                lines.append("")
            elif csi.get("pvc_total", 0) > 0:
                lines.append("✅ 所有 {} 个 PVC 已 Bound".format(csi["pvc_total"]))
                lines.append("")

        # ── 系统组件 ──
        sysc = r.get("system_components", {})
        if sysc.get("components"):
            _hdr(lines, "🔧 {} — 系统组件".format(r["name"]), 3)
            _table(lines, ["组件", "Ready", "Desired", "状态"],
                   [["{}".format(c["label"]),
                     c["ready"], c["desired"],
                     "✅" if c["healthy"] else "⚠️"]
                    for c in sysc["components"]])

        if sysc.get("high_restarts"):
            _hdr(lines, "🔄 高重启 Pod (>20次)", 4)
            _table(lines, ["Namespace", "Name", "Restarts", "Phase"],
                   [[h["namespace"], h["name"], h["restarts"], h["phase"]]
                    for h in sysc["high_restarts"][:10]])

        # ── 非 Running Pod ──
        nrp = r.get("non_running_pods", {})
        if nrp.get("pods"):
            _hdr(lines, "🚨 {} — 非 Running Pod".format(r["name"]), 3)
            _table(lines, ["Namespace", "Name", "Phase", "Reason", "Restarts"],
                   [[p["namespace"], p["name"][:40], p["phase"], p["reason"], p["restarts"]]
                    for p in nrp["pods"][:20]])

        # ── Rancher 管理组件 ──
        rh = r.get("rancher_health", {})
        if rh.get("components"):
            _hdr(lines, "🐄 {} — Rancher 管理组件".format(r["name"]), 3)
            rows = []
            for key, info in rh["components"].items():
                icon = "✅" if info["healthy"] else "⚠️"
                rows.append([key, info["phase"], "{}/{}".format(info["ready"], info["total"]),
                            info["restarts"], icon])
            _table(lines, ["组件", "Phase", "Ready", "Restarts", "状态"], rows)

        # ── 工作负载 ──
        wl = r.get("workloads", {})
        if wl.get("mismatched"):
            _hdr(lines, "📦 {} — 副本异常".format(r["name"]), 3)
            _table(lines, ["Kind", "Namespace/Name", "Ready/Desired"],
                   [["{}".format(m["kind"]),
                     "{}/{}".format(m["namespace"], m["name"]),
                     "{}/{}".format(m["ready"], m["desired"])]
                    for m in wl["mismatched"][:20]])

        # ── 事件 ──
        ev = r.get("events", {})
        if ev.get("ctrl_warnings"):
            _hdr(lines, "🚨 {} — 控制平面/etcd Warning 事件".format(r["name"]), 3)
            for w in ev["ctrl_warnings"][:10]:
                lines.append("- 🟡 **[{}]** {}: {}".format(
                    w["reason"], w["name"][:50],
                    (w["message"] or "")[:100]))
            lines.append("")
        if ev.get("warnings"):
            _hdr(lines, "📋 {} — 最近 Warning 事件".format(r["name"]), 3)
            for w in ev["warnings"][:10]:
                lines.append("- **[{}]** {}/{}: {}".format(
                    w["reason"], w["namespace"], w["name"],
                    (w["message"] or "")[:100]))
            lines.append("")

        # ── 项目 ──
        non_system = [p for p in r.get("projects", []) if not p["system"]]
        if non_system:
            _hdr(lines, "📦 {} — 项目 ({} 个)".format(r["name"], len(non_system)), 3)
            _table(lines, ["项目", "状态", "成员数"],
                   [["{}".format(p["name"]),
                     "✅" if p["state"] == "active" else "⚠️ " + p["state"],
                     "{}".format(p["member_count"])]
                    for p in non_system])

        # ── RBAC ──
        rb = r.get("rbac", {})
        if rb.get("rbac_cluster"):
            _hdr(lines, "🔐 {} — 集群级角色".format(r["name"]), 3)
            _table(lines, ["角色", "绑定数"],
                   [[role, count] for role, count in sorted(rb["rbac_cluster"].items())])

    return "\n".join(lines)


def generate_json(results, version, url, deep):
    return json.dumps({
        "rancher_url": url,
        "rancher_version": version,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "deep_check": deep,
        "clusters": results,
    }, indent=2, ensure_ascii=False, default=str)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rancher 集群巡检 (完整版)")
    parser.add_argument("-o", "--output", help="输出文件 (.md / .json)")
    parser.add_argument("-e", "--env", help="env 文件路径")
    parser.add_argument("-c", "--cluster", action="append", help="限定集群")
    parser.add_argument("--json", action="store_true", help="JSON 格式")
    parser.add_argument("--no-deep", action="store_true",
                        help="跳过 k8s API 深度检查 (仅 Rancher 级别)")
    args = parser.parse_args()

    url, token = load_env(args.env)
    version = get_rancher_version(url, token)
    deep = not args.no_deep

    print("# Rancher: {} ({})".format(url, version), file=sys.stderr)
    print("# 深度检查: {}".format("是" if deep else "否"), file=sys.stderr)
    print("# 正在巡检...", file=sys.stderr)

    clusters = get_clusters(url, token)
    cf = set(args.cluster) if args.cluster else None

    results = []
    for cl in clusters:
        cid   = cl["id"]
        cname = cl.get("name", cid)
        if cf:
            if cid not in cf and cname not in cf:
                continue
        print("  # 检查: {} ({})".format(cname, cid), file=sys.stderr)
        r = check_cluster(url, token, cl, deep)
        results.append(r)

    # 输出
    if args.json or (args.output and args.output.endswith(".json")):
        out = generate_json(results, version, url, deep)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
            print("# 已输出: {}".format(args.output), file=sys.stderr)
        else:
            print(out)
    else:
        out = generate_report(results, version, url, deep)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
            print("# 已输出: {}".format(args.output), file=sys.stderr)
        else:
            print(out)

    critical_count = sum(1 for r in results for i in r["issues"] if i["level"] == CRITICAL)
    if critical_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
