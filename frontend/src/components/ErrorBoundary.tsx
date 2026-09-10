import React from 'react';
import { strings } from '../i18n/strings';
import { ActionButton, AppText, Heading, Screen } from './ui/Primitives';

type ErrorBoundaryProps = {
  children: React.ReactNode;
  onError?: (diagnostic: { code: 'RENDER_FAILURE' }) => void;
};

type ErrorBoundaryState = {
  hasError: boolean;
  retryCount: number;
};

export class AppErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false, retryCount: 0 };

  static getDerivedStateFromError(): Partial<ErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch() {
    this.props.onError?.({ code: 'RENDER_FAILURE' });
  }

  handleRetry = () => {
    this.setState(state => ({
      hasError: false,
      retryCount: state.retryCount + 1,
    }));
  };

  render() {
    if (this.state.hasError) {
      return (
        <Screen testID="error-boundary-screen">
          <Heading>{strings.errors.renderTitle}</Heading>
          <AppText style={{ marginTop: 12 }}>
            {strings.errors.renderBody}
          </AppText>
          <ActionButton
            label={strings.errors.restart}
            onPress={this.handleRetry}
            style={{ marginTop: 24 }}
          />
        </Screen>
      );
    }

    return (
      <React.Fragment key={this.state.retryCount}>
        {this.props.children}
      </React.Fragment>
    );
  }
}
