package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "launcher: "+format+"\n", args...)
	os.Exit(1)
}

func findProjectDir(start string) string {
	dir := start
	for {
		if info, err := os.Stat(filepath.Join(dir, "pyproject.toml")); err == nil && !info.IsDir() {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func findUv() string {
	if p, err := exec.LookPath("uv.exe"); err == nil {
		return p
	}
	if p, err := exec.LookPath("uv"); err == nil {
		return p
	}
	fallback := filepath.Join(os.Getenv("USERPROFILE"), ".local", "bin", "uv.exe")
	if _, err := os.Stat(fallback); err == nil {
		return fallback
	}
	return ""
}

func main() {
	exePath, err := os.Executable()
	if err != nil {
		fail("cannot locate executable: %v", err)
	}
	exeDir := filepath.Dir(exePath)

	projectDir := findProjectDir(exeDir)
	if projectDir == "" {
		fail("no pyproject.toml found in %s or any parent directory", exeDir)
	}

	command := strings.TrimSuffix(filepath.Base(exePath), filepath.Ext(filepath.Base(exePath)))

	uvPath := findUv()
	if uvPath == "" {
		fail("uv not found in PATH or %%USERPROFILE%%\\.local\\bin")
	}

	args := append([]string{"run", "--project", projectDir, "--", command}, os.Args[1:]...)
	cmd := exec.Command(uvPath, args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		}
		fail("%v", err)
	}
}
