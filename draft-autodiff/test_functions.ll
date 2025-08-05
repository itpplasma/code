; ModuleID = 'FIRModule'
source_filename = "FIRModule"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"
target triple = "arm64-apple-macosx15.0.0"

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read)
define double @_QMautodiff_functionsPf_fortran(ptr nocapture readonly %0) local_unnamed_addr #0 {
  %2 = load double, ptr %0, align 8, !tbaa !3
  %3 = fmul contract double %2, %2
  %4 = fmul contract double %2, 2.000000e+00
  %5 = fadd contract double %3, %4
  %6 = fadd contract double %5, 1.000000e+00
  ret double %6
}

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read)
define double @_QMautodiff_functionsPg_fortran(ptr nocapture readonly %0) local_unnamed_addr #0 {
  %2 = load double, ptr %0, align 8, !tbaa !9
  %3 = tail call contract double @llvm.sin.f64(double %2)
  %4 = tail call contract double @llvm.cos.f64(double %2)
  %5 = fmul contract double %3, %4
  ret double %5
}

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read)
define double @_QMautodiff_functionsPh_fortran(ptr nocapture readonly %0) local_unnamed_addr #0 {
  %2 = load double, ptr %0, align 8, !tbaa !15
  %3 = tail call contract double @llvm.exp.f64(double %2)
  %4 = fadd contract double %2, 1.000000e+00
  %5 = tail call contract double @llvm.log.f64(double %4)
  %6 = fadd contract double %3, %5
  ret double %6
}

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read)
define double @_QMautodiff_functionsPf2_fortran(ptr nocapture readonly %0, ptr nocapture readonly %1) local_unnamed_addr #0 {
  %3 = load double, ptr %0, align 8, !tbaa !21
  %4 = fmul contract double %3, %3
  %5 = load double, ptr %1, align 8, !tbaa !27
  %6 = fmul contract double %5, %5
  %7 = fadd contract double %4, %6
  %8 = fmul contract double %3, %5
  %9 = fadd contract double %8, %7
  ret double %9
}

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read)
define double @_QMautodiff_functionsPg2_fortran(ptr nocapture readonly %0, ptr nocapture readonly %1) local_unnamed_addr #0 {
  %3 = load double, ptr %0, align 8, !tbaa !29
  %4 = tail call contract double @llvm.sin.f64(double %3)
  %5 = load double, ptr %1, align 8, !tbaa !35
  %6 = tail call contract double @llvm.cos.f64(double %5)
  %7 = fmul contract double %4, %6
  %8 = fmul contract double %3, %5
  %9 = tail call contract double @llvm.exp.f64(double %8)
  %10 = fadd contract double %7, %9
  ret double %10
}

; Function Attrs: mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read)
define double @_QMautodiff_functionsPh2_fortran(ptr nocapture readonly %0, ptr nocapture readonly %1) local_unnamed_addr #0 {
  %3 = load double, ptr %0, align 8, !tbaa !37
  %4 = fmul contract double %3, %3
  %5 = load double, ptr %1, align 8, !tbaa !43
  %6 = fmul contract double %5, %5
  %7 = fadd contract double %4, %6
  %8 = fadd contract double %7, 1.000000e+00
  %9 = tail call contract double @llvm.log.f64(double %8)
  %10 = fmul contract double %3, %5
  %11 = fadd contract double %3, %5
  %12 = tail call contract double @llvm.sin.f64(double %11)
  %13 = fmul contract double %10, %12
  %14 = fadd contract double %13, %9
  ret double %14
}

; Function Attrs: mustprogress nocallback nofree nosync nounwind speculatable willreturn memory(none)
declare double @llvm.sin.f64(double) #1

; Function Attrs: mustprogress nocallback nofree nosync nounwind speculatable willreturn memory(none)
declare double @llvm.cos.f64(double) #1

; Function Attrs: mustprogress nocallback nofree nosync nounwind speculatable willreturn memory(none)
declare double @llvm.exp.f64(double) #1

; Function Attrs: mustprogress nocallback nofree nosync nounwind speculatable willreturn memory(none)
declare double @llvm.log.f64(double) #1

attributes #0 = { mustprogress nofree norecurse nosync nounwind willreturn memory(argmem: read) "frame-pointer"="non-leaf" "target-cpu"="apple-m1" "target-features"="+zcm,+zcz,+v8.4a,+aes,+altnzcv,+ccdp,+ccpp,+complxnum,+crc,+dotprod,+flagm,+fp-armv8,+fp16fml,+fptoint,+fullfp16,+jsconv,+lse,+neon,+pauth,+perfmon,+predres,+ras,+rcpc,+rdm,+sb,+sha2,+sha3,+specrestrict,+ssbs" }
attributes #1 = { mustprogress nocallback nofree nosync nounwind speculatable willreturn memory(none) }

!llvm.module.flags = !{!0, !1}
!llvm.ident = !{!2}

!0 = !{i32 2, !"Debug Info Version", i32 3}
!1 = !{i32 8, !"PIC Level", i32 2}
!2 = !{!"Homebrew flang version 20.1.8 (https://github.com/Homebrew/homebrew-core/issues)"}
!3 = !{!4, !4, i64 0}
!4 = !{!"dummy arg data/_QMautodiff_functionsFf_fortranEx", !5, i64 0}
!5 = !{!"dummy arg data", !6, i64 0}
!6 = !{!"any data access", !7, i64 0}
!7 = !{!"any access", !8, i64 0}
!8 = !{!"Flang function root _QMautodiff_functionsPf_fortran"}
!9 = !{!10, !10, i64 0}
!10 = !{!"dummy arg data/_QMautodiff_functionsFg_fortranEx", !11, i64 0}
!11 = !{!"dummy arg data", !12, i64 0}
!12 = !{!"any data access", !13, i64 0}
!13 = !{!"any access", !14, i64 0}
!14 = !{!"Flang function root _QMautodiff_functionsPg_fortran"}
!15 = !{!16, !16, i64 0}
!16 = !{!"dummy arg data/_QMautodiff_functionsFh_fortranEx", !17, i64 0}
!17 = !{!"dummy arg data", !18, i64 0}
!18 = !{!"any data access", !19, i64 0}
!19 = !{!"any access", !20, i64 0}
!20 = !{!"Flang function root _QMautodiff_functionsPh_fortran"}
!21 = !{!22, !22, i64 0}
!22 = !{!"dummy arg data/_QMautodiff_functionsFf2_fortranEx", !23, i64 0}
!23 = !{!"dummy arg data", !24, i64 0}
!24 = !{!"any data access", !25, i64 0}
!25 = !{!"any access", !26, i64 0}
!26 = !{!"Flang function root _QMautodiff_functionsPf2_fortran"}
!27 = !{!28, !28, i64 0}
!28 = !{!"dummy arg data/_QMautodiff_functionsFf2_fortranEy", !23, i64 0}
!29 = !{!30, !30, i64 0}
!30 = !{!"dummy arg data/_QMautodiff_functionsFg2_fortranEx", !31, i64 0}
!31 = !{!"dummy arg data", !32, i64 0}
!32 = !{!"any data access", !33, i64 0}
!33 = !{!"any access", !34, i64 0}
!34 = !{!"Flang function root _QMautodiff_functionsPg2_fortran"}
!35 = !{!36, !36, i64 0}
!36 = !{!"dummy arg data/_QMautodiff_functionsFg2_fortranEy", !31, i64 0}
!37 = !{!38, !38, i64 0}
!38 = !{!"dummy arg data/_QMautodiff_functionsFh2_fortranEx", !39, i64 0}
!39 = !{!"dummy arg data", !40, i64 0}
!40 = !{!"any data access", !41, i64 0}
!41 = !{!"any access", !42, i64 0}
!42 = !{!"Flang function root _QMautodiff_functionsPh2_fortran"}
!43 = !{!44, !44, i64 0}
!44 = !{!"dummy arg data/_QMautodiff_functionsFh2_fortranEy", !39, i64 0}
