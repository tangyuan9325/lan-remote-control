package protocol

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
)

const (
	MsgJSON    = 0x01
	MsgJPEG    = 0x02
	MsgFile    = 0x03
	MsgAudio   = 0x04
	HeaderSize = 5
	// MaxMessageSize 最大消息大小：64MB（防止超大消息导致内存耗尽 DoS）
	MaxMessageSize = 64 * 1024 * 1024
)

var ErrMessageTooLarge = errors.New("message too large")

func PackJSON(obj map[string]interface{}) []byte {
	payload, _ := json.Marshal(obj)
	buf := make([]byte, HeaderSize+len(payload))
	buf[0] = MsgJSON
	binary.BigEndian.PutUint32(buf[1:5], uint32(len(payload)))
	copy(buf[5:], payload)
	return buf
}

func PackJPEG(data []byte) []byte {
	buf := make([]byte, HeaderSize+len(data))
	buf[0] = MsgJPEG
	binary.BigEndian.PutUint32(buf[1:5], uint32(len(data)))
	copy(buf[5:], data)
	return buf
}

func PackFile(data []byte) []byte {
	buf := make([]byte, HeaderSize+len(data))
	buf[0] = MsgFile
	binary.BigEndian.PutUint32(buf[1:5], uint32(len(data)))
	copy(buf[5:], data)
	return buf
}

func PackAudio(data []byte) []byte {
	buf := make([]byte, HeaderSize+len(data))
	buf[0] = MsgAudio
	binary.BigEndian.PutUint32(buf[1:5], uint32(len(data)))
	copy(buf[5:], data)
	return buf
}

func RecvMessage(conn net.Conn) (byte, []byte, error) {
	header := make([]byte, HeaderSize)
	if _, err := io.ReadFull(conn, header); err != nil {
		return 0, nil, err
	}
	msgType := header[0]
	length := binary.BigEndian.Uint32(header[1:5])

	// 安全检查：拒绝超大消息，防止内存耗尽 DoS
	if length > MaxMessageSize {
		return 0, nil, ErrMessageTooLarge
	}

	payload := make([]byte, length)
	if _, err := io.ReadFull(conn, payload); err != nil {
		return 0, nil, err
	}
	return msgType, payload, nil
}

func SendJSON(conn net.Conn, obj map[string]interface{}) error {
	_, err := conn.Write(PackJSON(obj))
	return err
}

func SendJPEG(conn net.Conn, data []byte) error {
	_, err := conn.Write(PackJPEG(data))
	return err
}

// Version 是协议/组件的统一版本号，所有 discovery 响应与握手消息
// 必须引用此常量，避免各端硬编码不一致（此前 Go 端为 1.3.0、webui 为 1.2、
// Android/Windows 为 1.2.0）。
const Version = "1.3.0"
