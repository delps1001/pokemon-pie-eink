docker buildx build \
  --platform linux/arm64 \
  -t delps1001/pokemon-eink-calendar:latest \
  --push .