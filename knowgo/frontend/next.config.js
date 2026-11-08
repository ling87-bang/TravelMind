/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 生产环境开启响应压缩，减少传输体积
  compress: true,
  poweredByHeader: false,
  async rewrites() {
    const backend = (process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000').replace(/\/+$/, '');
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/image/:path*', destination: `${backend}/image/:path*` },
    ];
  },
  images: {
    // 本项目无本地图片，SVG 全部内联，外链资源不做 next/image 优化
    unoptimized: true,
  },
  // 打包层面：对体积大的三方库做导入优化，拆出独立 chunk
  experimental: {
    // Agent turns can wait on external tools or a model for longer than 30 seconds.
    proxyTimeout: 180_000,
    optimizePackageImports: ['react-window', '@reduxjs/toolkit', 'react-redux'],
  },
  // 注意：不在 dev 模式为 /_next/static 配置自定义 Cache-Control 头。
  // 该头会让浏览器以 immutable 方式缓存旧 chunk，导致 Turbopack 热更新后
  // 出现 "module factory is not available" 的陈旧模块错误。
};

module.exports = nextConfig;
