import os
import json
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
import uvicorn
from pydantic import BaseModel

app = FastAPI()

# 跨域支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TokenUpdateRequest(BaseModel):
    X_LENOVO_SESS_ID: str
    S: str

METADATA_BASE_URL = "https://pan.bnuz.edu.cn/v2/metadata_page/databox"
FILE_BASE_URL = "https://pan.bnuz.edu.cn:10081/v2/files/databox/"


def load_tokens():
    if not os.path.exists("token.json"):
        raise RuntimeError("token.json 文件不存在")
    with open("token.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        return {
            "X-LENOVO-SESS-ID": config.get("X-LENOVO-SESS-ID", ""),
            "S": config.get("S", "")
        }

COOKIES = load_tokens()

PARAMS = {
    "path_type": "self",
    "page_button_count": 5,
    "include_deleted": "false",
    "page_size": 20,
    "waitContent": ".lui-filelist",
    "page_num": 0,
    "offset": 0,
    "sort": "desc",
    "orderby": "mtime",
    "account_id": 1,
    "uid": 000000,
    "S": COOKIES["S"]
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://pan.bnuz.edu.cn/folder/self",
}


def get_dir_content(base_url, path="/"):
    full_url = base_url + quote(path)
    response = requests.get(
        full_url,
        params=PARAMS,
        headers=HEADERS,
        cookies=COOKIES,
        timeout=10
    )
    if response.status_code == 200:
        return response.json().get("content", [])
    raise RuntimeError(f"请求目录失败：{response.status_code}")


def simplify_structure(data):
    result = []
    for item in data:
        name = os.path.basename(item.get("path", "").rstrip("/"))
        node = {
            "name": name,
            "neid": item.get("neid"),
            "path": item.get("path"),
            "type": "dir" if item.get("is_dir") else "file"
        }
        if item.get("is_dir") and item.get("content"):
            node["children"] = simplify_structure(item["content"])
        result.append(node)
    return result



def load_directory_tree():
    if not os.path.exists("directory_tree.json"):
        return []
    with open("directory_tree.json", "r", encoding="utf-8") as f:
        return json.load(f)

def save_directory_tree(data):
    with open("directory_tree.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_node_by_path(tree, path):
    if path == "/":
        return tree
    parts = [p for p in path.strip("/").split("/") if p]
    current = tree
    for part in parts:
        match = next((node for node in current if node["name"] == part and node["type"] == "dir"), None)
        if not match:
            return None
        current = match.get("children", [])
    return match

def add_content(base_url, file_list):
    for item in file_list:
        # 从路径提取目录名，path最后一段就是目录名
        dir_name = os.path.basename(item.get("path", "").rstrip("/"))

        print(f"遍历目录名：{dir_name} 路径：{item.get('path')}")

        if item.get("is_dir") and dir_name == "百度网盘":
            print(f"跳过目录：{item['path']}（百度网盘）")
            continue

        if item.get('is_dir'):
            print(f"处理目录：{item['path']}")
            sub_content = get_dir_content(base_url, item['path'])
            if sub_content:
                item['content'] = sub_content
                add_content(base_url, sub_content)
def fetch_full_directory_tree():
    """
    从根目录开始递归爬取完整目录结构并简化结构
    """
    root_content = get_dir_content(METADATA_BASE_URL, "/")
    if not root_content:
        raise RuntimeError("无法获取根目录内容")

    # 递归填充所有目录内容
    add_content(METADATA_BASE_URL, root_content)

    # 转换成精简格式
    return simplify_structure(root_content)
@app.post("/api/update_token")
def update_token(data: TokenUpdateRequest):
    try:
        with open("token.json", "w", encoding="utf-8") as f:
            json.dump({"X-LENOVO-SESS-ID": data.X_LENOVO_SESS_ID, "S": data.S}, f, ensure_ascii=False, indent=2)

        global COOKIES, PARAMS
        COOKIES = load_tokens()
        PARAMS["S"] = COOKIES["S"]

        return {"status": "success", "message": "Token 已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tree")
def get_tree(path: str = "/"):
    tree = load_directory_tree()
    node = find_node_by_path(tree, path)
    if node is None:
        raise HTTPException(status_code=404, detail=f"路径不存在: {path}")
    if isinstance(node, list):
        return {"status": "success", "data": node}
    if node.get("type") != "dir":
        raise HTTPException(status_code=400, detail="该路径不是目录")
    return {"status": "success", "data": node.get("children", [])}

@app.get("/api/refresh")
def refresh_directory(path: str = "/"):
    """
    只刷新当前目录（不递归子目录）
    """
    try:
        # 获取当前目录下的文件和子目录（不递归）
        new_content = get_dir_content(METADATA_BASE_URL, path)

        # 仅保留当前层的简化结构
        simplified = simplify_structure(new_content)

        # 特别处理根目录（它是一个 list）
        if path == "/":
            save_directory_tree(simplified)
            return {"status": "success", "message": "根目录已刷新"}

        # 非根目录：找到树中的节点，替换其 children
        tree = load_directory_tree()
        sub_node = find_node_by_path(tree, path)

        if not sub_node:
            raise HTTPException(status_code=404, detail=f"路径不存在: {path}")

        if sub_node.get("type") != "dir":
            raise HTTPException(status_code=400, detail="该路径不是目录")

        # 仅更新这一层
        sub_node["children"] = simplified

        # 写回文件
        save_directory_tree(tree)

        return {"status": "success", "message": f"目录 {path} 已刷新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新失败: {str(e)}")
@app.get("/api/refresh_all")
def refresh_all_directories():
    """
    刷新整个目录树（从根目录开始递归）
    """
    try:
        new_tree = fetch_full_directory_tree()
        save_directory_tree(new_tree)
        return {"status": "success", "message": "已刷新全部目录树"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新失败: {str(e)}")

@app.get("/api/link")
def get_direct_link(path: str = Query(...), neid: str = Query(...)):
    full_url = FILE_BASE_URL + quote(path)
    params = {
        "X-LENOVO-SESS-ID": COOKIES["X-LENOVO-SESS-ID"],
        "path_type": "self",
        "neid": neid,
        "S": COOKIES["S"]
    }
    try:
        response = requests.head(
            full_url,
            params=params,
            headers=HEADERS,
            cookies=COOKIES,
            allow_redirects=True,
            timeout=10
        )
        if response.status_code == 200:
            return {"status": "success", "url": response.url}
        raise HTTPException(status_code=response.status_code, detail="获取直链失败")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("sdyp-api:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")
