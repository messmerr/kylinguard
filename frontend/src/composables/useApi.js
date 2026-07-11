// 单用户本机应用的统一请求入口。
export function apiFetch(url, options = {}) {
  return fetch(url, options)
}
