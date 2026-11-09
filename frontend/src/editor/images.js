// 粘贴 / 拖入正文的图片：在浏览器里缩小到长边不超过 1600px 后转成 data URI 内嵌到正文
// （发信时服务端转成邮件内嵌附件）。透明的小 PNG 保留原格式，其余压成 JPEG。
const MAX_EDGE = 1600;
const KEEP_PNG_UNDER = 300 * 1024;
const JPEG_QUALITY = 0.85;

function readAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("无法读取图片"));
    img.src = url;
  });
}

export function isImageFile(file) {
  return /^image\/(png|jpe?g|gif|webp|bmp)$/i.test(file?.type || "");
}

export async function imageToDataUrl(file) {
  const original = await readAsDataUrl(file);
  // GIF 可能是动图：不重新编码
  if (/gif/i.test(file.type)) return original;
  const img = await loadImage(original);
  const scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
  if (scale === 1 && file.size <= KEEP_PNG_UNDER) return original;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.naturalWidth * scale);
  canvas.height = Math.round(img.naturalHeight * scale);
  const ctx = canvas.getContext("2d");
  const keepPng = /png/i.test(file.type) && file.size <= KEEP_PNG_UNDER;
  if (!keepPng) {
    ctx.fillStyle = "#fff";   // JPEG 没有透明：透明处填白
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL(keepPng ? "image/png" : "image/jpeg", JPEG_QUALITY);
}
