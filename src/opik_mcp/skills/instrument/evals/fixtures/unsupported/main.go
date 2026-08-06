// A Go program — unsupported by the Opik instrument skill (Python/TypeScript only).
// The skill must return `unsupported` and modify NOTHING.
package main

import "fmt"

func answer(question string) string {
	return "Answer to: " + question
}

func main() {
	fmt.Println(answer("What is your refund window?"))
}
