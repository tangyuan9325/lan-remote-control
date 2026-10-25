package inputinject

// floatOf 安全地将 JSON 解出的 interface{} 转为 float64，缺失/类型错误时返回 0
func floatOf(v interface{}) float64 {
	if f, ok := v.(float64); ok {
		return f
	}
	return 0
}

// stringOf 安全地将 JSON 解出的 interface{} 转为 string，缺失/类型错误时返回 ""
func stringOf(v interface{}) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
